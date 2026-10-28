# ATL-7 — read-only OKX v5 REST client (`trading_core.okx`)

Async httpx, no python-okx at runtime (it is only used in a test to cross-check signatures).
Phase 0 is **read-only**: no order, amend, cancel, leverage, transfer or withdrawal method exists, and a
test fails if one is added to `OkxClient`.

## Build a client

```python
from trading_core.okx import OkxClient, OkxCredentials, client_for_connection

# public only (instruments, tickers, time) — no credentials
async with OkxClient("demo") as okx:
    swaps = await okx.get_instruments("SWAP")

# from a connections row (decision #235): row needs .env, .api_key, .api_secret, .passphrase,
# each a Laravel `encrypted`-cast ciphertext. APP_KEY is the "base64:..." string.
async with client_for_connection(row, os.environ["APP_KEY"]) as okx:
    cfg = await okx.get_account_config()
```

If the `connections` table names its columns differently, call
`credentials_from_ciphertexts(api_key_ct, secret_ct, passphrase_ct, app_key)` and pass the result to
`OkxClient(env, creds)`. `OkxCredentials.__repr__` shows only the last 4 characters of the key.

`env` must be a key of `DEFAULTS["okx"]["environments"]` (`demo`, `live`). Demo sends
`x-simulated-trading: 1`; live sends no such header (both from `config/defaults.yaml`).

Share one `RateLimiter()` between clients that use the same key (OKX counts private limits per user ID):
`OkxClient(env, creds, rate_limiter=shared)`.

## Methods and return models (all in `trading_core.okx.models`)

| Method | Endpoint | Returns |
| --- | --- | --- |
| `get_server_time()` | public/time | `ServerTime` (`ts`, `.epoch_ms`) |
| `sync_clock()` | public/time | `int` offset ms (server minus local) |
| `get_instruments(inst_type, *, inst_family, inst_id)` | public/instruments | `list[Instrument]` |
| `get_tickers(inst_type, *, inst_family)` | market/tickers | `list[Ticker]` |
| `get_account_config()` | account/config | `AccountConfig` (`acct_lv`, `pos_mode`, `perm`, `.permissions`, `uid`) |
| `get_balance(ccy=None)` | account/balance | `AccountBalance` (`total_eq`, ..., `details: list[BalanceDetail]`) |
| `get_positions(*, inst_type, inst_id, pos_id)` | account/positions | `list[Position]` |
| `get_positions_history(*, inst_type, inst_id, after, before, limit)` | account/positions-history | `list[PositionHistory]` |
| `get_bills(*, inst_type, ccy, type, after, before, limit)` | account/bills (7 days) | `list[Bill]` |
| `get_bills_archive(...same...)` | account/bills-archive (3 months) | `list[Bill]` |
| `get_trade_fee(inst_type, *, inst_id, inst_family)` | account/trade-fee | `TradeFee` |
| `get_asset_balances(ccy=None)` | asset/balances (funding account) | `list[AssetBalance]` |
| `get_deposit_history(*, ccy, after, before, limit)` | asset/deposit-history | `list[DepositRecord]` |
| `get_withdrawal_history(*, ccy, after, before, limit)` | asset/withdrawal-history (read only) | `list[WithdrawalRecord]` |

Model rules: fields are snake_case of the OKX camelCase names (`totalEq` → `total_eq`, `eqUsd` → `eq_usd`).
Exceptions: `Instrument.max_lever` ← OKX `lever`; `DepositRecord.from_`/`WithdrawalRecord.from_` ← `from`;
`TradeFee.maker_usdc` ← `makerUSDC`. Every number is `Decimal | None` (OKX `""` → `None`; a float input
is refused). Every ms timestamp is an aware UTC `datetime`. Unknown OKX fields are ignored.
Fees from trade-fee are negative rates (`-0.0005` = you pay 0.05 %).

## Errors (`trading_core.okx.errors`, all subclass `OkxClientError`)

- `OkxError(code, msg, path, http_status)` — body `code != "0"` (e.g. `50113` Invalid Sign, any HTTP status).
- `OkxHttpError(status)` — HTTP error with no OKX body (after retries for 5xx/429).
- `OkxNetworkError(reason)` — no HTTP answer after every retry.
- `OkxResponseError` — 2xx with a body that is not `{code, msg, data: [...]}`.

No message ever contains the key, secret, passphrase or signature.

## Retries, rate limits, clock (`config/defaults.yaml` → `okx_rest`)

- `max_attempts: 3` total. Retried: `httpx.TransportError`, HTTP 5xx, HTTP 429, and OKX codes in
  `retryable_codes` (50001, 50011, 50013). Full-jitter backoff: `rand() * min(8, 0.5 * 2**(n-1))` s.
- Token bucket per endpoint, from `okx_rest.rate_limits` (documented OKX limits, e.g. account/balance
  10 per 2 s, account/bills 5 per 1 s). A new endpoint **must** get an entry there, or `RateLimiter`
  raises `KeyError`.
- Signed calls sync the clock from public/time on first use and again after
  `clock_sync_interval_seconds: 600`; `OK-ACCESS-TIMESTAMP` = local ms + offset, ISO 8601 with ms and `Z`.

## Fixtures

`services/trading_core/tests/fixtures/okx/` — see its README: recorded from the demo API (scrubbed) for
time, instruments, tickers, config, balance, bills, bills-archive, trade-fee; hand-written in the
documented shape for positions, positions-history, funding balances, deposits, withdrawals (the demo
account returned empty lists).

## Two-minute version

- Paths in code are written without `/api/v5/` (`"asset/withdrawal-history"`); the repo-wide
  NoWithdrawalCodeTest bans the literal `/api/v5/asset/withdrawal`, which is a prefix of the history path.
  Keep it that way.
- The owner's demo key reports `perm: "read_only,withdraw,trade"` from account/config, and the demo
  account equity is about 98.7k USDT, not 5M. ATL-8's Test connection should surface the permissions.
