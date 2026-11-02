<?php

declare(strict_types=1);

// ATL-222: رسائل التحقق من الإعدادات وأسماء حقولها بلغة بسيطة، بلا رمز حقل يحوي شرطات سفلية.
return [
    'decimal_format_positive' => 'يجب أن تكون قيمة :attribute موجبة، ومكتوبة كرقم مثل "1.5".',
    'decimal_format_exclusive' => 'يجب أن تكون قيمة :attribute رقمًا عشريًا أكبر من :min، مثل "1.5".',
    'decimal_format_inclusive' => 'يجب أن تكون قيمة :attribute رقمًا عشريًا لا يقل عن :min، مثل "1.5".',
    'decimal_range_exclusive' => 'يجب أن تكون قيمة :attribute أكبر من :min وبحد أقصى :max.',
    'decimal_range_inclusive' => 'يجب أن تكون قيمة :attribute بين :min و:max.',
    'stale_version' => 'تم تغيير الإعدادات في مكان آخر. أعد تحميلها وطبّق تغييرك مرة أخرى.',
    'first_live_month_autonomy' => 'خلال الشهر الأول من التداول الحقيقي (حتى :until) لا يمكن أن يتجاوز مستوى الاستقلالية الحقيقي المستوى :max.',
    'first_live_month_cap_required' => 'خلال الشهر الأول من التداول الحقيقي (حتى :until) يجب أن يبقى حدّ رأس مال البوت الحقيقي محددًا.',
    'autonomy_level_locked' => 'المستوى :max هو أعلى مستوى استقلالية تدعمه هذه المرحلة من البناء.',
    'hostname_format' => 'يجب أن يكون كل عنصر اسم مضيف بسيطًا مثل "coindesk.com"، بلا بروتوكول أو منفذ أو مسار.',
    'inst_id_format' => 'يجب أن يكون كل عنصر رمز أداة OKX مثل "BTC-USDT-SWAP".',
    'blocklist_until_format' => 'يجب أن تربط كل صلاحية حظر رمزَ أداة بوقت ISO 8601 مع منطقة زمنية، مثل "2026-11-01T20:59:59Z".',
    'blocklist_until_past' => 'يجب أن يكون تاريخ انتهاء الحظر في المستقبل.',

    // ATL-228: الإعدادات ← الجدول الزمني (بتوقيت بغداد).
    'schedule_time_format' => 'يجب أن يكون :attribute وقتًا بين 00:00 و23:59 بصيغة HH:MM مثل "07:30".',
    'schedule_weekly_format' => 'يجب أن يكون :attribute يومًا من الأسبوع ووقتًا، مثل "Sun 18:00".',
    'schedule_first_before_last' => 'يجب أن يكون :earlier قبل :later.',
    'schedule_scout_after_brief' => 'يجب أن يكون :later بعد :earlier، لأن مستكشف السوق يقرأ الموجز الصباحي.',
    'schedule_scout_after_regime' => 'يجب أن يكون :later بعد :earlier، لأن مستكشف السوق يقرأ جدول أنظمة السوق.',
    'schedule_quiet_empty' => 'يجب أن تبدأ ساعات الهدوء وتنتهي في وقتين مختلفين.',

    'attr_agent_setting' => ':agent: :setting',
    'attr_notification' => ':event: :channel',

    'agents' => [
        'orchestrator' => 'المنسّق',
        'market_scout' => 'مستكشف السوق',
        'news_intelligence' => 'محلّل الأخبار',
        'historian' => 'المؤرّخ',
        'technical_analyst' => 'المحلّل الفني',
        'derivatives_sentiment' => 'المشتقات والمعنويات',
        'strategist' => 'الاستراتيجي',
        'devils_advocate' => 'محامي الشيطان',
        'portfolio_manager' => 'مدير المحفظة',
    ],

    'agent_setting' => [
        'enabled' => 'مفعّل',
        'model' => 'النموذج',
        'daily_tokens' => 'الرموز اليومية',
        'daily_turns' => 'الدورات اليومية',
    ],

    'notification_event' => [
        'proposal_awaiting_approval' => 'صفقة تنتظر الموافقة',
        'order_filled' => 'تنفيذ أمر',
        'stop_hit' => 'بلوغ وقف الخسارة',
        'target_hit' => 'بلوغ الهدف',
        'circuit_breaker' => 'قاطع الدائرة',
        'kill_switch' => 'إيقاف التداول',
        'daily_report' => 'التقرير اليومي',
        'news_alert' => 'خبر عن مركز تملكه',
        'agent_degraded' => 'تعطّل وكيل',
        'data_stale' => 'توقف بيانات السوق',
        'reconciliation_mismatch' => 'اختلاف أطلس وOKX',
    ],

    'notification_channel' => [
        'in_app' => 'داخل أطلس',
        'browser' => 'المتصفح',
        'telegram' => 'تيليجرام',
    ],

    'attr' => [
        'risk_profile' => 'ملف المخاطر',

        'max_loss_per_trade_pct' => 'أقصى خسارة للصفقة',
        'max_position_pct' => 'أقصى حجم للمركز',
        'max_daily_loss_pct' => 'أقصى خسارة يومية',
        'max_weekly_loss_pct' => 'أقصى خسارة أسبوعية',
        'max_leverage' => 'أقصى رافعة',
        'max_open_positions' => 'أقصى عدد للمراكز المفتوحة',
        'max_share_per_instrument_pct' => 'أقصى حصة في أداة واحدة',
        'max_correlated_exposure_pct' => 'أقصى انكشاف مترابط',
        'min_expected_r' => 'أدنى عائد متوقع (R)',
        'max_consecutive_losses' => 'خسائر متتالية قبل الإيقاف',

        'autonomy_level' => 'مستوى الاستقلالية',
        'autonomy_ask_above_usd' => 'اسألني فوق',

        'max_instruments_per_day' => 'أقصى عدد من الأدوات في اليوم',
        'max_spread_bps' => 'أقصى فارق سعر',
        'min_24h_volume_usd_crypto_perp' => 'الحد الأدنى لحجم التداول في 24 ساعة (العقود الدائمة للعملات المشفرة)',
        'min_24h_volume_usd_stock_perp' => 'الحد الأدنى لحجم التداول في 24 ساعة (العقود الدائمة للأسهم والصناديق والمؤشرات)',
        'min_24h_volume_usd_commodity_perp' => 'الحد الأدنى لحجم التداول في 24 ساعة (العقود الدائمة للسلع)',
        'min_24h_volume_usd_spot' => 'الحد الأدنى لحجم التداول في 24 ساعة (السوق الفورية)',
        'avoid_stock_perps_outside_us_hours' => 'تجنّب العقود الدائمة للأسهم خارج ساعات السوق الأمريكية',
        'enabled_markets' => 'فئات الأصول',
        'whitelist' => 'الأدوات المسموح بها',
        'blocklist' => 'الأدوات المحظورة',
        'blocklist_until' => 'مواقيت انتهاء الحظر',

        'bot_capital_cap_usdt' => 'سقف رأس المال',
        'max_usdt_per_trade' => 'الحد الأقصى للصفقة',
        'max_new_exposure_per_day_usdt' => 'أقصى تعرّض جديد في اليوم',
        'max_trades_per_day' => 'أقصى عدد للصفقات في اليوم',

        'min_stop_atr_mult' => 'الحد الأدنى لمسافة وقف الخسارة (×ATR)',
        'max_stop_pct' => 'الحد الأقصى لمسافة وقف الخسارة',
        'slippage_bps' => 'الانزلاق السعري المفترض',
        'liq_buffer_mult' => 'هامش أمان التصفية',
        'block_new_trades_minutes_before_high_impact_event' => 'فترة حظر الصفقات قبل حدث مهم',
        'volatility_halt_sigma' => 'حد إيقاف التقلب',
        'market_data_max_age_seconds' => 'أقصى عمر لبيانات السوق',
        'margin_buffer' => 'هامش أمان الرهن',

        'entry_timeout_minutes' => 'مهلة أمر الدخول',
        'td_mode_perps' => 'نمط الهامش للعقود الدائمة',
        'stop_verify_seconds' => 'مهلة التحقق من وقف الخسارة',
        'order_max_retries' => 'أقصى عدد لإعادة محاولة الأمر',
        'emergency_stop_atr_mult' => 'مسافة وقف الطوارئ (×ATR)',

        'onboarding_completed' => 'اكتمال الإعداد الأولي',

        // ATL-228: الإعدادات ← الجدول الزمني.
        'schedule' => [
            'news_morning_brief' => 'وقت الموجز الإخباري الصباحي',
            'historian_regime_table' => 'وقت جدول أنظمة السوق من المؤرّخ',
            'scout_watchlist' => 'وقت قائمة المراقبة من مستكشف السوق',
            'pm_daily_report' => 'وقت التقرير اليومي لمدير المحفظة',
            'historian_weekly_review' => 'المراجعة الأسبوعية للمؤرّخ',
            'analysis_interval_minutes' => 'الفاصل بين دورات التحليل',
            'analysis_first_slot' => 'أول دورة تحليل',
            'analysis_last_slot' => 'آخر دورة تحليل',
            'quiet_hours_enabled' => 'ساعات الهدوء',
            'quiet_hours_start' => 'بداية ساعات الهدوء',
            'quiet_hours_end' => 'نهاية ساعات الهدوء',
            'stock_perps_weekend' => 'عقود الأسهم الدائمة في عطلة نهاية الأسبوع',
        ],

        'agents' => [
            'web' => [
                'allow_domains' => 'المواقع المسموح بها',
                'deny_domains' => 'المواقع المحظورة',
            ],
        ],
    ],

    // ATL-222 (جولة فحص الجودة الأولى): اسم مفرد لكل عنصر في حقل قائمة.
    'attr_element' => [
        'enabled_markets' => 'فئة الأصل',
        'whitelist' => 'أداة مسموح بها',
        'blocklist' => 'أداة محظورة',
        'agents' => [
            'web' => [
                'allow_domains' => 'موقع مسموح به',
                'deny_domains' => 'موقع محظور',
            ],
        ],
    ],
];
