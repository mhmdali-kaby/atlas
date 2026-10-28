# OKX v5 REST fixtures (ATL-7)

Recorded once from the OKX **demo** API on 2026-10-28 (`x-simulated-trading: 1`), then scrubbed:
`uid`/`mainUid` replaced with zeros, `label` replaced, instrument and ticker lists trimmed to six
instruments. No API key, secret or passphrase is in any file.

Recorded: `public_time`, `public_instruments_swap`, `market_tickers_swap`, `account_config`,
`account_balance`, `account_bills`, `account_bills_archive`, `account_trade_fee_swap`.

Hand-written in the documented response shape, because the demo account returned an empty list:
`account_positions`, `account_positions_history`, `asset_balances`, `asset_deposit_history`,
`asset_withdrawal_history`, and `error_invalid_sign` (the documented 50113 body).
