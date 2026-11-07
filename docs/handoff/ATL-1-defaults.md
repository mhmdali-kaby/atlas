# ATL-1 — Atlas defaults (one YAML, generated PHP + Python)

## The two-minute version

- Edit **only** `config/defaults.yaml` (repo root). Then run `cd backend && php artisan atlas:defaults`.
- Commit the YAML **and** both generated files. `tests/Unit/AtlasDefaultsTest.php` fails if they are stale.
- `php artisan atlas:defaults --check` exits 1 and lists stale files without writing (handy for CI).
- Percentages, money, ratios and R multiples are **strings** (`"1.0"`), never floats. The generator refuses an
  unquoted float. Wrap them in `Brick\Math\BigDecimal::of(...)` (PHP) or `Decimal(...)` (Python) before maths.
- Percent values are in percent units: `"1.0"` = 1.0 % of equity, not 0.01.
- Counts, leverage (`max_leverage: 5` = 5x) and intervals in seconds are integers.

## Reading them

PHP: `config('atlas_defaults.<path>')`
```php
config('atlas_defaults.risk_profiles.balanced.max_loss_per_trade_pct'); // "1.0"
```
Python:
```python
from atlas_shared.defaults import DEFAULTS
DEFAULTS["risk_profiles"]["balanced"]["max_loss_per_trade_pct"]  # "1.0"
```

## Key paths

| Path | Value | Type |
| --- | --- | --- |
| `env.default` | `demo` | string |
| `env.allowed` | `[demo, live]` | list |
| `timezone.display` / `timezone.storage` | `Asia/Baghdad` / `UTC` | string |
| `risk_profiles.default` | `balanced` | string |
| `risk_profiles.<conservative\|balanced\|aggressive>.max_loss_per_trade_pct` | 0.5 / 1.0 / 2.0 | decimal string |
| `….max_position_pct` | 2 / 5 / 10 | decimal string |
| `….max_daily_loss_pct` | 2 / 4 / 8 | decimal string |
| `….max_weekly_loss_pct` | 5 / 10 / 20 | decimal string |
| `….max_leverage` | 2 / 5 / 10 | int |
| `….max_open_positions` | 3 / 6 / 10 | int |
| `….max_share_per_instrument_pct` | 15 / 25 / 40 | decimal string |
| `….max_correlated_exposure_pct` | 20 / 35 / 50 | decimal string |
| `….min_expected_r` | 2.0 / 1.8 / 1.5 | decimal string |
| `….max_consecutive_losses` | 2 / 3 / 4 | int |
| `autonomy.default_level` | `2` | int |
| `autonomy.levels` | `[1, 2, 3, 4]` | list |
| `autonomy.on_approval_timeout` | `reject` | string |
| `markets.min_24h_volume_usd.crypto_perp` | `"20000000"` | decimal string (USD) |
| `markets.min_24h_volume_usd.stock_perp` / `.commodity_perp` | `"5000000"` | decimal string |
| `markets.min_24h_volume_usd.spot` | `"10000000"` | decimal string |
| `markets.max_spread_bps` | `"8"` | decimal string |
| `markets.max_instruments_per_day` (`_min` / `_max`) | 5 (1 / 10) | int |
| `okx.environments.<demo\|live>.rest_base_url` | `https://www.okx.com` | string |
| `okx.environments.<env>.ws_public_url` / `ws_private_url` / `ws_business_url` | demo `wss://wspap.okx.com:8443/ws/v5/…`, live `wss://ws.okx.com:8443/ws/v5/…` | string |
| `okx.environments.demo.headers` | `{"x-simulated-trading": "1"}` | map (live: empty map) |
| `sync.instruments_interval_seconds` | 3600 | int |
| `sync.account_interval_seconds` | 60 | int |
| `sync.bills_interval_seconds` | 300 | int |

Send every header in `okx.environments.<env>.headers` on every REST call for that env; don't special-case demo.

## What is not here

Risk Engine knobs outside the §14.3 table (min_stop_atr_mult, max_stop_pct, liq_buffer_mult, event blackout),
money limits (USDT), schedule times and agent budgets were not in this task's scope. Add them to the YAML when the
task that uses them lands, then regenerate. In PHP an empty map renders as `[]`; in Python it is `{}`.
