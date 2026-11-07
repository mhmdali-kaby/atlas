<?php

declare(strict_types=1);

// ATL-236: /api/environment — the live pre-flight and the guarded switch.
return [
    'fields' => [
        'target' => 'environment',
        'password' => 'password',
        'confirmation' => 'confirmation text',
        'confirm' => 'confirmation',
        'ip_allowlist' => 'IP allow-list',
        'sub_account' => 'sub-account',
        'demo_reviewed' => 'demo results reviewed',
    ],
    'envs' => [
        'demo' => 'Demo',
        'live' => 'Live',
    ],
    'profiles' => [
        'conservative' => 'Conservative',
        'balanced' => 'Balanced',
        'aggressive' => 'Aggressive',
        'custom' => 'Custom',
    ],
    'items' => [
        'live_connection' => 'Live connection verified with withdraw permission off',
        'demo_history' => 'At least 4 weeks of demo history',
        'live_risk_profile' => 'Live risk profile chosen',
        'live_autonomy' => 'Live autonomy at level 2 or lower',
        'live_capital_cap' => 'Live bot capital cap set',
        'ip_allowlist' => 'IP allow-list set on the OKX key',
        'sub_account' => 'Trading from a dedicated sub-account',
        'demo_reviewed' => 'Demo results reviewed',
    ],
    'preflight' => [
        'live_connection' => [
            'missing' => 'No live OKX connection. Add one in Settings → Connections.',
            'unverified' => 'The live OKX connection is not verified. Test it in Settings → Connections.',
            'withdraw' => 'OKX has not confirmed that the live key cannot withdraw. Test it again with a Read + Trade key.',
            'ok' => 'Verified by OKX, with no withdraw permission.',
        ],
        'demo_history' => [
            'none' => 'No demo equity history yet. :days days are needed.',
            'short' => ':have of :days days of demo history.',
            'ok' => ':have days of demo history.',
        ],
        'live_risk_profile' => [
            'fail' => 'No live risk profile yet. Choose one in Settings → Risk with Live selected.',
            'ok' => 'Live risk profile: :profile.',
        ],
        'live_autonomy' => [
            'none' => 'Live autonomy is not set yet. Choose level :max or lower.',
            'fail' => 'Live autonomy is level :level. The first live month allows level :max or lower.',
            'ok' => 'Live autonomy is level :level.',
        ],
        'live_capital_cap' => [
            'fail' => 'No live bot capital cap. Set one in Settings → Money limits with Live selected.',
            'ok' => 'Live bot capital cap: :cap USDT.',
        ],
        'attested' => [
            'ok' => 'Confirmed by the owner at :at.',
        ],
        'ip_allowlist' => [
            'fail' => 'Confirm that the live OKX key is restricted to your IP addresses.',
        ],
        'sub_account' => [
            'fail' => 'Confirm that the live key belongs to a sub-account used only by Atlas.',
        ],
        'demo_reviewed' => [
            'fail' => 'Confirm that you have reviewed the demo results.',
        ],
    ],
    'switch' => [
        'already' => 'Atlas is already trading in :env.',
        'password' => 'The password is incorrect.',
        'confirmation' => 'Type :text exactly, in capital letters.',
        'preflight_item' => 'Pre-flight not passed: :item.',
        'pause_reason' => 'Paused after switching environment. Resume when you are ready.',
    ],
];
