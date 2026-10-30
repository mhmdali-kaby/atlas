"""Long/short account ratio, taker volume and open interest, polled every 5 minutes (ATL-134, §7.5).

``DerivativesPoller.tick()`` is the scheduler job (every ``derivatives_poller.interval_seconds``).
Each tick reads the instrument set the market feed subscribes to (today's watchlist picks the owner
has not removed and the open positions, ``trading_core.ws.subscriptions``) and works out:

* the base currencies: ``derivatives_poller.always_ccys`` (BTC, ETH) plus the base currency of every
  instrument in the set. Instruments the ``instruments`` table classes as anything but ``crypto``
  are skipped, because OKX rubik statistics exist for crypto currencies only. For each currency it
  reads ``rubik/stat/contracts/long-short-account-ratio`` and ``rubik/stat/taker-volume``
  (``taker_inst_type`` CONTRACTS) at ``period`` and upserts on the tables' natural keys
  ``(env, ccy, period, ts)``, so a repeated poll of the same payload changes nothing. A row OKX
  sends without a value is skipped (the columns are NOT NULL).
* the perpetuals (``-SWAP``) in the set, stock and commodity perps included: ``public/open-interest``
  per instrument, one ``open_interest`` row per poll keyed by OKX's ``ts``. The backfill keeps only
  rubik's 1D currency series, which cannot give a 1h or 4h OI change; these 5-minute rows are what
  ``atlas_agents.derivatives`` reads for the OI-price quadrant.

Every request goes through the shared, rate-limited ``OkxClient``. One currency or instrument
failing does not stop the others; it is counted in ``failed``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from atlas_shared.db import Instrument
from atlas_shared.db import LongShortRatio as LongShortRow
from atlas_shared.db import OpenInterest as OpenInterestRow
from atlas_shared.db import TakerVolume as TakerRow
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, select

from trading_core.backfill.runner import upsert_rows
from trading_core.okx import OkxClient
from trading_core.ws.subscriptions import desired_instruments

logger = logging.getLogger("trading_core.marketdata.derivatives")

JOB = "derivatives_poller"

ClientFactory = Callable[[str], OkxClient]
InstrumentSource = Callable[[], set[str]]


def derivatives_cfg() -> dict[str, Any]:
    cfg: dict[str, Any] = DEFAULTS["derivatives_poller"]
    return cfg


def base_ccy(inst_id: str) -> str:
    """``BTC-USDT-SWAP`` -> ``BTC`` (OKX leaves baseCcy empty on SWAP rows, so the id is the source)."""
    return inst_id.split("-", 1)[0].upper()


@dataclass
class DerivativesPollResult:
    env: str
    ccys: list[str] = field(default_factory=list)
    inst_ids: list[str] = field(default_factory=list)
    long_short_rows: int = 0
    taker_rows: int = 0
    oi_rows: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "ccys": self.ccys,
            "inst_ids": self.inst_ids,
            "long_short_rows": self.long_short_rows,
            "taker_rows": self.taker_rows,
            "oi_rows": self.oi_rows,
            "failed": self.failed,
        }


class DerivativesPoller:
    def __init__(
        self,
        env: str,
        engine: Engine,
        *,
        client_factory: ClientFactory | None = None,
        instruments: InstrumentSource | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        cfg = derivatives_cfg()
        self.env = env
        self.engine = engine
        self._factory: ClientFactory = client_factory or OkxClient
        self._now = now or (lambda: datetime.now(UTC))
        self._instruments: InstrumentSource = instruments or (
            lambda: desired_instruments(engine, env, self._now())
        )
        self.period = str(cfg["period"])
        self.taker_inst_type = str(cfg["taker_inst_type"])
        self.always = [str(c).upper() for c in cfg["always_ccys"]]
        self.concurrency = max(1, int(cfg["concurrency"]))

    def _non_crypto(self, inst_ids: Iterable[str]) -> set[str]:
        ids = sorted(set(inst_ids))
        if not ids:
            return set()
        with self.engine.connect() as conn:
            return set(
                conn.execute(
                    select(Instrument.inst_id).where(
                        Instrument.env == self.env,
                        Instrument.inst_id.in_(ids),
                        Instrument.asset_class != "crypto",
                    )
                ).scalars()
            )

    def targets(self) -> tuple[list[str], list[str]]:
        """(currencies, perpetuals): always_ccys first, then the crypto base currencies of the
        watched/held instruments, sorted; and every watched/held ``-SWAP`` instrument, sorted."""
        inst_ids = self._instruments()
        skip = self._non_crypto(inst_ids)
        extra = sorted({base_ccy(i) for i in inst_ids if i not in skip} - set(self.always))
        swaps = sorted(i for i in inst_ids if i.upper().endswith("-SWAP"))
        return [*self.always, *extra], swaps

    def _write(self, model: type[Any], rows: list[dict[str, Any]]) -> int:
        with self.engine.begin() as conn:
            return upsert_rows(conn, model, rows)

    async def _poll_ccy(self, okx: OkxClient, ccy: str, result: DerivativesPollResult) -> None:
        try:
            ls = await okx.get_long_short_ratio(ccy, period=self.period)
            ls_rows = [
                {"env": self.env, "ccy": ccy, "period": self.period, "ts": r.ts, "ratio": r.ratio}
                for r in ls
                if r.ratio is not None
            ]
            taker = await okx.get_taker_volume(ccy, inst_type=self.taker_inst_type, period=self.period)
            taker_rows = [
                {
                    "env": self.env,
                    "ccy": ccy,
                    "period": self.period,
                    "ts": r.ts,
                    "buy_vol": r.buy_vol,
                    "sell_vol": r.sell_vol,
                }
                for r in taker
                if r.buy_vol is not None and r.sell_vol is not None
            ]
            # Await first, then add: ``x += await ...`` reads x before the await and loses updates.
            ls_written = await asyncio.to_thread(self._write, LongShortRow, ls_rows)
            taker_written = await asyncio.to_thread(self._write, TakerRow, taker_rows)
            result.long_short_rows += ls_written
            result.taker_rows += taker_written
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result.failed += 1
            logger.warning(
                "derivatives poll failed",
                extra={"ccy": ccy, "error": f"{type(exc).__name__}: {exc}"[:500]},
            )

    async def _poll_oi(self, okx: OkxClient, inst_id: str, result: DerivativesPollResult) -> None:
        try:
            page = await okx.get_open_interest("SWAP", inst_id=inst_id)
            rows = [
                {
                    "env": self.env,
                    "inst_id": r.inst_id,
                    "ts": r.ts,
                    "oi": r.oi,
                    "oi_ccy": r.oi_ccy,
                    "oi_usd": r.oi_usd,
                }
                for r in page
                if r.inst_id == inst_id
            ]
            written = await asyncio.to_thread(self._write, OpenInterestRow, rows)
            result.oi_rows += written
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result.failed += 1
            logger.warning(
                "open interest poll failed",
                extra={"inst_id": inst_id, "error": f"{type(exc).__name__}: {exc}"[:500]},
            )

    async def tick(self) -> dict[str, Any]:
        ccys, swaps = await asyncio.to_thread(self.targets)
        result = DerivativesPollResult(self.env, ccys, swaps)
        gate = asyncio.Semaphore(self.concurrency)
        async with self._factory(self.env) as okx:

            async def one_ccy(ccy: str) -> None:
                async with gate:
                    await self._poll_ccy(okx, ccy, result)

            async def one_oi(inst_id: str) -> None:
                async with gate:
                    await self._poll_oi(okx, inst_id, result)

            await asyncio.gather(*(one_ccy(c) for c in ccys), *(one_oi(i) for i in swaps))
        if result.failed:
            logger.info("derivatives polled", extra=result.as_dict())
        return result.as_dict()


def derivatives_poller_job(
    env: str,
    engine: Engine,
    *,
    client_factory: ClientFactory | None = None,
    instruments: InstrumentSource | None = None,
) -> Callable[[], Any]:
    poller = DerivativesPoller(env, engine, client_factory=client_factory, instruments=instruments)
    return poller.tick


__all__ = ["JOB", "DerivativesPollResult", "DerivativesPoller", "base_ccy", "derivatives_poller_job"]
