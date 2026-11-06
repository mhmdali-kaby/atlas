<?php

declare(strict_types=1);

return [
    // ATL-222: the format rule fires before a value is even parsed as a number (e.g. "-1", which
    // the pattern never allows), so it cannot yet show the field's upper bound — only its lower
    // one. decimal_range_* below show the full range once the value parses.
    'decimal_format_positive' => 'The :attribute must be a positive amount, written as a number such as "1.5".',
    'decimal_format_exclusive' => 'The :attribute must be a decimal number greater than :min, such as "1.5".',
    'decimal_format_inclusive' => 'The :attribute must be a decimal number of :min or more, such as "1.5".',
    'decimal_range_exclusive' => 'The :attribute must be greater than :min and at most :max.',
    'decimal_range_inclusive' => 'The :attribute must be between :min and :max.',
    'stale_version' => 'Settings were changed elsewhere. Reload them and apply your change again.',
    'first_live_month_autonomy' => 'During the first live month (until :until) live autonomy can be at most level :max.',
    'first_live_month_cap_required' => 'During the first live month (until :until) the live bot capital cap must stay set.',
    'autonomy_level_locked' => 'Autonomy level :max is the highest this build phase supports.',
    'hostname_format' => 'Each entry must be a bare hostname such as "coindesk.com", with no scheme, port or path.',
    'inst_id_format' => 'Each entry must be an OKX instrument ID such as "BTC-USDT-SWAP".',
    'blocklist_until_format' => 'Each blocklist expiry must map an instrument ID to an ISO 8601 time with a time zone, such as "2026-11-01T20:59:59Z".',
    'blocklist_until_past' => 'A blocklist expiry must be in the future.',
    // ATL-231: Settings → Backup.
    'backup_folder_absolute' => 'The :attribute must be a full path starting with "/", such as "/Users/you/Atlas Backups".',
    'backup_folder_inside_repo' => 'The :attribute must be outside the Atlas folder, so backups are never stored next to the code.',
    'backup_folder_unwritable' => 'Atlas cannot write to the :attribute. Choose a folder you can write to, or one that can be created.',
    'backup_time_format' => 'The :attribute must be a time between 00:00 and 23:59, written as HH:MM such as "03:00".',

    // ATL-228: Settings → Schedule (Baghdad times).
    'schedule_time_format' => 'The :attribute must be a time between 00:00 and 23:59, written as HH:MM such as "07:30".',
    'schedule_weekly_format' => 'The :attribute must be a weekday and a time, such as "Sun 18:00".',
    'schedule_first_before_last' => 'The :earlier must be earlier than the :later.',
    'schedule_scout_after_brief' => 'The :later must be later than the :earlier, because Market Scout reads the morning brief.',
    'schedule_scout_after_regime' => 'The :later must be later than the :earlier, because Market Scout reads the regime table.',
    'schedule_quiet_empty' => 'Quiet hours must start and end at different times.',

    // ATL-222: SettingsLabels composes these two for agents.<slug>.<setting> and
    // notifications.<event>.<channel>, so nine agents and eleven events don't need 69 separate
    // attribute translations.
    'prompt_version_unknown' => 'Prompt version :version is not one of this agent\'s saved versions.',
    'attr_agent_setting' => ':agent: :setting',
    'attr_notification' => ':event: :channel',

    // The nine roster agents (config/defaults.yaml agents.roster; names match the War Room's
    // @mention roster so an agent is called the same thing everywhere).
    'agents' => [
        'orchestrator' => 'Orchestrator',
        'market_scout' => 'Market Scout',
        'news_intelligence' => 'News Intelligence',
        'historian' => 'Historian',
        'technical_analyst' => 'Technical Analyst',
        'derivatives_sentiment' => 'Derivatives & Sentiment',
        'strategist' => 'Strategist',
        'devils_advocate' => "Devil's Advocate",
        'portfolio_manager' => 'Portfolio Manager',
    ],

    // SettingsSchema::AGENT_SETTINGS, each agent's four owner-editable settings.
    'agent_setting' => [
        'enabled' => 'on',
        'model' => 'model',
        'daily_tokens' => 'daily tokens',
        'daily_turns' => 'daily turns',
        'prompt_version' => 'prompt version',
    ],

    // Brief §17's eleven notification events (config/defaults.yaml notifications.events).
    'notification_event' => [
        'proposal_awaiting_approval' => 'trade waiting for approval',
        'order_filled' => 'order filled',
        'stop_hit' => 'stop-loss hit',
        'target_hit' => 'target hit',
        'circuit_breaker' => 'circuit breaker',
        'kill_switch' => 'trading stopped',
        'daily_report' => 'daily report',
        'news_alert' => 'news on a held position',
        'agent_degraded' => 'agent not working',
        'data_stale' => 'market data stopped',
        'reconciliation_mismatch' => 'Atlas and OKX disagreeing',
        'backup_failed' => 'database backup failing',
    ],

    'notification_channel' => [
        'in_app' => 'in Atlas',
        'browser' => 'browser',
        'telegram' => 'Telegram',
    ],

    // Every plain settings field's human name (SettingsSchema::fields()), read by SettingsLabels.
    'attr' => [
        'risk_profile' => 'Risk profile',

        // §14.3 risk profile fields.
        'max_loss_per_trade_pct' => 'Max loss per trade',
        'max_position_pct' => 'Max position size',
        'max_daily_loss_pct' => 'Max daily loss',
        'max_weekly_loss_pct' => 'Max weekly loss',
        'max_leverage' => 'Max leverage',
        'max_open_positions' => 'Max open positions',
        'max_share_per_instrument_pct' => 'Max share in one instrument',
        'max_correlated_exposure_pct' => 'Max correlated exposure',
        'min_expected_r' => 'Min expected R',
        'max_consecutive_losses' => 'Losses in a row before pause',

        // §14.6 autonomy.
        'autonomy_level' => 'Autonomy level',
        'autonomy_ask_above_usd' => 'Ask me above',

        // §14.5 markets.
        'max_instruments_per_day' => 'Max instruments per day',
        'max_spread_bps' => 'Max spread',
        'min_24h_volume_usd_crypto_perp' => 'Minimum 24h volume (crypto perpetuals)',
        'min_24h_volume_usd_stock_perp' => 'Minimum 24h volume (stock, ETF and index perpetuals)',
        'min_24h_volume_usd_commodity_perp' => 'Minimum 24h volume (commodity perpetuals)',
        'min_24h_volume_usd_spot' => 'Minimum 24h volume (spot)',
        'avoid_stock_perps_outside_us_hours' => 'Avoid stock perpetuals outside US hours',
        'enabled_markets' => 'Asset classes',
        'whitelist' => 'Allowed instruments',
        'blocklist' => 'Blocked instruments',
        'blocklist_until' => 'Blocklist expiry times',

        // §14.4 money limits.
        'bot_capital_cap_usdt' => 'Bot capital cap',
        'max_usdt_per_trade' => 'Max per trade',
        'max_new_exposure_per_day_usdt' => 'Max new exposure per day',
        'max_trades_per_day' => 'Max trades per day',

        // §10 Risk Engine parameters.
        'min_stop_atr_mult' => 'Minimum stop distance (×ATR)',
        'max_stop_pct' => 'Maximum stop distance',
        'slippage_bps' => 'Assumed slippage',
        'liq_buffer_mult' => 'Liquidation safety buffer',
        'block_new_trades_minutes_before_high_impact_event' => 'Event blackout window',
        'volatility_halt_sigma' => 'Volatility halt threshold',
        'market_data_max_age_seconds' => 'Max market data age',
        'margin_buffer' => 'Margin safety buffer',

        // §11 Executor parameters.
        'entry_timeout_minutes' => 'Entry order timeout',
        'td_mode_perps' => 'Perpetuals margin mode',
        'stop_verify_seconds' => 'Stop verification window',
        'order_max_retries' => 'Max order retries',
        'emergency_stop_atr_mult' => 'Emergency stop distance (×ATR)',

        'onboarding_completed' => 'Onboarding completed',

        'backup' => [
            'enabled' => 'Daily backup',
            'folder' => 'Backup folder',
            'time' => 'Backup time',
            'keep' => 'Number of backups to keep',
        ],

        // ATL-228: Settings → Schedule.
        'schedule' => [
            'news_morning_brief' => 'News morning brief time',
            'historian_regime_table' => 'Historian regime table time',
            'scout_watchlist' => 'Market Scout watchlist time',
            'pm_daily_report' => 'Portfolio Manager daily report time',
            'historian_weekly_review' => 'Historian weekly review',
            'analysis_interval_minutes' => 'Analysis interval',
            'analysis_first_slot' => 'First analysis cycle',
            'analysis_last_slot' => 'Last analysis cycle',
            'quiet_hours_enabled' => 'Quiet hours',
            'quiet_hours_start' => 'Quiet hours start',
            'quiet_hours_end' => 'Quiet hours end',
            'stock_perps_weekend' => 'Stock perpetuals at the weekend',
        ],

        'agents' => [
            'web' => [
                'allow_domains' => 'Allowed web domains',
                'deny_domains' => 'Blocked web domains',
            ],
        ],
    ],

    // ATL-222 (QA round 1): the singular name of one entry in a list field, read by
    // SettingsLabels::forListElement so "changes.whitelist.0 field is required" instead reads
    // "The Allowed instrument field is required."
    'attr_element' => [
        'enabled_markets' => 'Asset class',
        'whitelist' => 'Allowed instrument',
        'blocklist' => 'Blocked instrument',
        'agents' => [
            'web' => [
                'allow_domains' => 'Allowed web domain',
                'deny_domains' => 'Blocked web domain',
            ],
        ],
    ],
];
