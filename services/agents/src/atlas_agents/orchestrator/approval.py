"""What happens to a proposal after its risk check (brief §7.9 / §10 check 9; ATL-160, ATL-169, ATL-213).

The single owner of the approval policy. Both an analysis cycle's entry (``CycleRunner._risk``) and a
Portfolio Manager amend/close (``pm.route.route``) call :func:`apply_risk_result` with the body of
the signed ``POST /internal/risk/evaluate/{id}``:

* ``result == "rejected"`` → ``rejected`` with ``status_reason`` ``risk_rejected``;
* autonomy 1, or the engine's ``decision == "advise_only"`` → ``advise_only``;
* ``decision == "send_now"`` at autonomy 3-4 (decision #254), when the body's risk check is still the
  proposal's newest → ``sent`` (no ``approval_expires_at``), then exactly one signed
  ``POST /internal/proposals/{id}/execute`` with ``approved_by = "autonomy:<level>"`` — the route the
  owner's Approve uses. It re-runs the Risk Engine and calls the Executor only if the fresh check
  says ``send_now``. Its answer settles the proposal:

  - 200 ``sent`` → the proposal stays with the Executor (``sent`` → ``filled``…); War Room: sent
    automatically, with the level and the notional;
  - 409 ``needs_owner`` (the fresh check now asks for the owner) → ``awaiting_owner`` with an
    approval window, from the fresh check;
  - any other refusal or failure → trading_core already closed it (``rejected`` / ``risk_rejected``
    or ``execution_<reason>``); a proposal still ``sent`` (trading_core never re-checked it, e.g.
    it was unreachable) is closed ``rejected`` / ``execution_<reason>`` here. War Room: not sent,
    and why;
* otherwise → ``awaiting_owner`` with ``approval_expires_at`` = now + the approval timeout.

The row only moves from ``risk_checked``, so a repeated call for the same risk check never executes
twice. It posts the ``risk_check`` War Room card and, for ``awaiting_owner``/``advise_only``, one
``proposal.{status}`` event on ``atlas.proposals``. Nothing here calls the Executor or OKX directly:
the only order path is trading_core's execute route (Executor ← fresh risk check).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from atlas_shared.db import Proposal
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from atlas_agents import proposals as proposal_reads
from atlas_agents.orchestrator.trading_core import TradingCoreClient, TradingCoreError
from atlas_agents.tools.framework import ToolContext
from atlas_agents.tools.warroom import message_envelope

log = logging.getLogger(__name__)

WARROOM_STREAM: Final = "atlas.warroom"
PROPOSALS_STREAM: Final = "atlas.proposals"
AUTONOMY_SEND_LEVELS: Final = (3, 4)


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def autonomy_level(session: Session, ctx: ToolContext, defaults: Mapping[str, Any] = DEFAULTS) -> int:
    """The owner's autonomy level: settings.data, else the typed column, else the default."""
    data = proposal_reads.settings_data(session, ctx).data
    row = session.execute(text("SELECT autonomy_level FROM settings WHERE env = :e"), {"e": ctx.env}).first()
    raw = data.get("autonomy_level", row[0] if row is not None else None)
    try:
        return int(raw) if raw is not None else int(defaults["autonomy"]["default_level"])
    except (TypeError, ValueError):
        return int(defaults["autonomy"]["default_level"])


def approval_timeout(session: Session, ctx: ToolContext, defaults: Mapping[str, Any] = DEFAULTS) -> timedelta:
    """settings.data.approval_timeout_minutes (at least 1 minute), else the default."""
    data = proposal_reads.settings_data(session, ctx).data
    raw = data.get("approval_timeout_minutes", defaults["autonomy"]["approval_timeout_minutes"])
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        minutes = int(defaults["autonomy"]["approval_timeout_minutes"])
    return timedelta(minutes=max(1, minutes))


def _decide(level: int, body: Mapping[str, Any]) -> str:
    if body.get("result") == "rejected":
        return "rejected"
    if level <= 1 or body.get("decision") == "advise_only":
        return "advise_only"
    if body.get("decision") == "send_now" and level in AUTONOMY_SEND_LEVELS:
        return "sent"
    return "awaiting_owner"


def _check_standing(session: Session, proposal_id: uuid.UUID, risk_check_id: Any) -> str:
    """``newest`` when ``risk_check_id`` is the proposal's newest stored risk check, ``superseded``
    when it is stored and a newer one exists (a stale or repeated body), else ``unknown``."""
    if not risk_check_id:
        return "unknown"
    rows = session.execute(
        text("SELECT id FROM risk_checks WHERE proposal_id = :p ORDER BY created_at DESC, id DESC"),
        {"p": proposal_id},
    ).scalars()
    ids = [str(r) for r in rows]
    if ids and ids[0] == str(risk_check_id):
        return "newest"
    return "superseded" if str(risk_check_id) in ids else "unknown"


def _record(
    ctx: ToolContext,
    proposal_id: uuid.UUID,
    body: Mapping[str, Any],
    *,
    autonomy: int | None,
    at: datetime,
    defaults: Mapping[str, Any],
) -> tuple[str, int, datetime | None, Any]:
    """Move the proposal from ``risk_checked`` to the status the result asks for (sync)."""
    with ctx.session_factory() as session:
        level = autonomy if autonomy is not None else autonomy_level(session, ctx, defaults)
        status = _decide(level, body)
        # Lock the row first: trading_core's re-check writes the new risk check and moves the row
        # back to risk_checked in one transaction, so after the lock the newest-check read below
        # sees both. A body whose check is no longer the newest (a repeat during or after an
        # execute) moves nothing.
        locked = session.execute(
            select(Proposal.status).where(Proposal.id == proposal_id).with_for_update()
        ).scalar()
        standing = _check_standing(session, proposal_id, body.get("risk_check_id"))
        if locked != "risk_checked" or standing == "superseded":
            session.rollback()
            return status, level, None, None
        if status == "sent" and standing != "newest":
            status = "awaiting_owner"  # autonomy only sends the check it can see is current
        expires = at + approval_timeout(session, ctx, defaults) if status == "awaiting_owner" else None
        values: dict[str, Any] = {"status": status, "updated_at": at}
        if expires is not None:
            values["approval_expires_at"] = expires
        if status == "rejected":
            values["status_reason"] = "risk_rejected"
        row = session.execute(
            update(Proposal)
            .where(Proposal.id == proposal_id, Proposal.status == "risk_checked")
            .values(**values)
            .returning(Proposal.id, Proposal.version, Proposal.inst_id, Proposal.side, Proposal.kind)
        ).first()
        session.commit()
    return status, level, expires, row


def _publish_status(
    ctx: ToolContext,
    row: Any,
    status: str,
    expires: datetime | None,
    body: Mapping[str, Any],
    *,
    cycle_id: uuid.UUID | str | None,
    source: str,
    at: datetime,
) -> None:
    ctx.publish(
        PROPOSALS_STREAM,
        EventEnvelope(
            type=f"proposal.{status}",
            ts=at,
            env=ctx.env,
            source=source,
            correlation_id=ctx.correlation_id,
            payload={
                "proposal_id": str(row.id),
                "version": row.version,
                "cycle_id": str(cycle_id) if cycle_id is not None else None,
                "kind": row.kind,
                "inst_id": row.inst_id,
                "side": row.side,
                "risk_check_id": body.get("risk_check_id"),
                "result": body.get("result"),
                "decision": body.get("decision"),
                "final_contracts": body.get("final_contracts"),
                "final_notional_usd": body.get("final_notional_usd"),
                "leverage": body.get("leverage"),
                "approval_expires_at": _iso(expires) if expires else None,
            },
        ),
    )


def _line(ctx: ToolContext, content: str, card: dict[str, Any]) -> None:
    ctx.publish(WARROOM_STREAM, message_envelope(ctx, kind="text", content_md=content, card=card))


def _what(row: Any) -> str:
    kind = row.kind or "entry"
    if kind == "entry":
        return f"{row.side} {row.inst_id}" if row.side else str(row.inst_id)
    return f"{kind.replace('_', ' ')} on {row.inst_id}"


def _close_unsent(
    ctx: ToolContext, proposal_id: uuid.UUID, risk_check_id: Any, reason: str, at: datetime
) -> tuple[bool, str | None]:
    """Close the proposal ``rejected`` / ``reason`` only if trading_core never touched it: it is
    still ``sent`` **and** ``risk_check_id`` (the check this call sent on) is still its newest risk
    check. The Executor also writes ``sent`` once it has placed the order, after the route's fresh
    re-check, so ``sent`` alone cannot tell "never reached trading_core" from "actually sent".

    Returns ``(closed, status afterwards)``. Under the row lock, so trading_core's re-check (which
    writes the new risk check and moves the row in one transaction) is either fully seen or not."""
    with ctx.session_factory() as session:
        current: str | None = session.execute(
            select(Proposal.status).where(Proposal.id == proposal_id).with_for_update()
        ).scalar()
        untouched = current == "sent" and _check_standing(session, proposal_id, risk_check_id) == "newest"
        if untouched:
            session.execute(
                update(Proposal)
                .where(Proposal.id == proposal_id, Proposal.status == "sent")
                .values(status="rejected", status_reason=reason[:64], updated_at=at)
            )
            current = "rejected"
        session.commit()
    return untouched, current


async def apply_risk_result(
    ctx: ToolContext,
    proposal_id: uuid.UUID,
    body: Mapping[str, Any],
    *,
    cycle_id: uuid.UUID | str | None,
    source: str,
    client: TradingCoreClient | None = None,
    now: datetime | None = None,
    autonomy: int | None = None,
    inst_id: str | None = None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> tuple[str, datetime | None, Any]:
    """Set the proposal's status from the risk result, publish the card and event, and at autonomy
    3-4 send a ``send_now`` through trading_core's execute route.

    Returns ``(status, approval_expires_at, row)``; ``row`` (id, version, inst_id, side, kind) is
    None when the proposal was no longer ``risk_checked`` (nothing is then published on
    atlas.proposals and nothing is sent). After an autonomy send, ``status`` is ``sent`` when the
    route sent it, else the status the proposal ended in (``error`` when a ``needs_owner`` answer
    found the row no longer ``risk_checked``). ``autonomy`` is read from the settings when
    not given; ``now`` defaults to ``ctx.now()``; ``client`` defaults to a :class:`TradingCoreClient`.
    """
    at = now or ctx.now()
    status, level, expires, row = await asyncio.to_thread(
        _record, ctx, proposal_id, body, autonomy=autonomy, at=at, defaults=defaults
    )

    inst = row.inst_id if row is not None else (inst_id or "?")
    card = {"type": "risk_check", **{k: v for k, v in body.items() if k != "ok"}}
    content = f"**Risk {body.get('result')}** on {inst}: {body.get('reason') or ''}".strip()
    ctx.publish(WARROOM_STREAM, message_envelope(ctx, kind="risk_check", content_md=content, card=card))
    if row is None:
        return status, expires, row
    if status in ("awaiting_owner", "advise_only"):
        _publish_status(ctx, row, status, expires, body, cycle_id=cycle_id, source=source, at=at)
    if status != "sent":
        return status, expires, row
    return await _send(
        ctx,
        proposal_id,
        row,
        body,
        level,
        cycle_id=cycle_id,
        source=source,
        client=client or TradingCoreClient(),
        defaults=defaults,
    )


async def _send(
    ctx: ToolContext,
    proposal_id: uuid.UUID,
    row: Any,
    body: Mapping[str, Any],
    level: int,
    *,
    cycle_id: uuid.UUID | str | None,
    source: str,
    client: TradingCoreClient,
    defaults: Mapping[str, Any],
) -> tuple[str, datetime | None, Any]:
    """The one execute call for a proposal this call moved to ``sent``."""
    approved_by = f"autonomy:{level}"
    what = _what(row)
    try:
        http, resp = await client.execute_proposal(str(proposal_id), approved_by)
    except TradingCoreError as exc:
        log.warning("autonomy: execute on %s failed: %s", proposal_id, exc.code)
        closed, final = await asyncio.to_thread(
            _close_unsent, ctx, proposal_id, body.get("risk_check_id"), f"execution_{exc.code}", ctx.now()
        )
        if closed:
            text_md = (
                f"Autonomy {level} could not send {what}: trading_core did not answer ({exc.code}) "
                "and never re-checked it, so nothing was sent. The proposal is rejected."
            )
        else:
            # trading_core re-checked it (and may have placed the order) before the answer was
            # lost: leave the row to trading_core; reconciliation with OKX settles it.
            text_md = (
                f"Autonomy {level} sent {what} to trading_core but got no answer ({exc.code}). "
                f"The outcome is unknown: the proposal is {final or 'unknown'} and reconciliation "
                "with OKX will settle it."
            )
        _line(
            ctx,
            text_md,
            {
                "type": "autonomy_execution",
                "proposal_id": str(proposal_id),
                "approved_by": approved_by,
                "autonomy_level": level,
                "status": "failed" if closed else "unknown",
                "reason": exc.code,
            },
        )
        return final or "rejected", None, row

    raw_fresh = resp.get("risk_check")
    fresh: Mapping[str, Any] | None = raw_fresh if isinstance(raw_fresh, Mapping) else None
    sized = fresh if fresh is not None else body
    notional = sized.get("final_notional_usd")
    contracts = sized.get("final_contracts")
    reason = str(resp.get("reason") or f"http_{http}")
    detail = str(resp.get("detail") or "").strip()
    card: dict[str, Any] = {
        "type": "autonomy_execution",
        "proposal_id": str(proposal_id),
        "approved_by": approved_by,
        "autonomy_level": level,
        "status": resp.get("status"),
        "reason": reason,
        "final_contracts": contracts,
        "final_notional_usd": notional,
        "risk_check_id": sized.get("risk_check_id"),
    }
    if http == 200 and resp.get("status") == "sent":
        _line(
            ctx,
            f"Sent automatically at autonomy {level}: {what}, {contracts} contracts, "
            f"notional {notional} USD. No owner approval was needed.",
            card,
        )
        return "sent", None, row

    if reason == "needs_owner" and fresh is not None:
        # The fresh check asks for the owner: the normal approval window, from that check.
        at = ctx.now()
        status, _level, expires, again = await asyncio.to_thread(
            _record, ctx, proposal_id, fresh, autonomy=2, at=at, defaults=defaults
        )
        if again is not None:
            _publish_status(ctx, again, status, expires, fresh, cycle_id=cycle_id, source=source, at=at)
        _line(
            ctx,
            f"Autonomy {level} did not send {what}: the fresh risk check asks for your approval "
            f"(notional {notional} USD). The proposal is {status if again is not None else 'unchanged'}.",
            card,
        )
        return (status if again is not None else "error"), expires, row

    # trading_core answered: it has already closed anything it re-checked. Only a row it never
    # re-checked (e.g. refused before the re-check) is closed here.
    _closed, final = await asyncio.to_thread(
        _close_unsent, ctx, proposal_id, body.get("risk_check_id"), f"execution_{reason}", ctx.now()
    )
    because = f": {detail}" if detail else ""
    _line(
        ctx,
        (
            f"Autonomy {level} sent {what}, but trading_core reported {reason}{because}. "
            "The proposal stays sent; reconciliation with OKX will settle it."
            if final == "sent"  # the Executor placed the order before failing (e.g. unprotected_closed)
            else f"Autonomy {level} did not send {what} ({reason}){because}. "
            f"The proposal is {final or 'rejected'}."
        ),
        card,
    )
    return final or "rejected", None, row


__all__ = [
    "AUTONOMY_SEND_LEVELS",
    "PROPOSALS_STREAM",
    "apply_risk_result",
    "approval_timeout",
    "autonomy_level",
]
