# OKX v5 REST fixtures (ATL-7)

Recorded once from the OKX **demo** API on 2026-10-28 (`x-simulated-trading: 1`), then scrubbed:
`uid`/`mainUid` replaced with zeros, `label` replaced, instrument and ticker lists trimmed to six
instruments. No API key, secret or passphrase is in any file.

Recorded: `public_time`, `public_instruments_swap`, `market_tickers_swap`, `account_config`,
`account_balance`, `account_bills`, `account_bills_archive`, `account_trade_fee_swap`.

Hand-written in the documented response shape, because the demo account returned an empty list:
`account_positions`, `account_positions_history`, `asset_balances`, `asset_deposit_history`,
`asset_withdrawal_history`, and `error_invalid_sign` (the documented 50113 body).

## Market data (ATL-45)

Hand-written in the documented OKX v5 response shape (the build sandbox had no network to record them):
`market_candles` (1H BTC-USDT-SWAP, newest first, first bar still forming with `confirm: "0"`),
`market_history_candles`, `public_funding_rate`, `public_funding_rate_history`, `public_open_interest`,
`public_mark_price`, and the positional-array rubik files `rubik_stat_contracts_open_interest_volume`,
`rubik_stat_contracts_long_short_account_ratio`, `rubik_stat_taker_volume`. Re-record from the demo API
when a networked run is available; the shapes must not change.

ATL-134 adds `market_index_tickers` (quoteCcy=USDT, three indexes), hand-written in the documented
shape for the same reason.
