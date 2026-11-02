<?php

declare(strict_types=1);

// Telegram messages and API errors (ATL-215). Messages are sent as plain text (no parse_mode), so
// params such as a news headline can never be read as markup. Times are shown in Asia/Baghdad.
return [
    'env' => [
        'demo' => 'DEMO',
        'live' => 'LIVE',
    ],
    'events' => [
        'proposal_awaiting_approval' => "Proposal awaiting your approval\n:side :inst_id (:kind), about :final_notional_usd USD.\nApprove before :approval_expires_at (Baghdad).",
        'order_filled' => "Order filled\n:side :filled_sz :inst_id at :avg_px (:purpose).",
        'stop_hit' => "Stop-loss hit\n:inst_id: :sz closed at :actual_px (stop :trigger_px).",
        'target_hit' => "Take-profit hit\n:inst_id: :sz closed at :actual_px (target :trigger_px).",
        'circuit_breaker' => "Circuit breaker tripped: :name\nNew orders are paused. :reason",
        'data_stale' => "Market data is stale\nNew orders are paused. :reason",
        'reconciliation_mismatch' => "Atlas and OKX disagree\nNew orders are paused until reconciliation clears it. :reason",
        'kill_switch' => "Kill switch used\nTrading is paused. Orders cancelled: :cancelled, positions closed: :closed, stops kept: :stops_kept, failures: :failed.",
        'agent_degraded' => "Agent :agent is :state\nThe team carries on without it.",
        'daily_report' => 'Daily report for :day is ready in the War Room.',
        'news_alert' => "News on a held instrument (importance :importance)\n:inst_ids — :headline (:source_name)",
        'backup_failed' => "Database backup failed\nOpen Settings → Backup in Atlas to see why.",
    ],
    'open' => 'Open: :url',
    'yes' => 'yes',
    'no' => 'no',
    'none' => '—',
    'buttons' => [
        'approve' => 'Approve',
        'reject' => 'Reject',
    ],
    'callback' => [
        'approve_in_atlas' => 'Live trade: approve it in Atlas. Reject still works here.',
        'expired' => 'expired',
        'approved' => 'Approved — the order was sent.',
        'approve_failed' => 'Not sent: :reason',
        'rejected' => 'Rejected.',
        'reject_failed' => 'Not rejected: :reason',
    ],
    'outcome' => [
        'approved' => 'Approved from Telegram at :time (Baghdad).',
        'approve_failed' => 'Approve from Telegram at :time (Baghdad) did not send the order: :reason',
        'rejected' => 'Rejected from Telegram at :time (Baghdad).',
    ],
    'reject_reason' => 'Rejected from Telegram.',
    'test_message' => 'Atlas test message: Telegram notifications are connected.',
    'not_configured' => 'Telegram is not set up. Add a bot token and chat id first.',
    'test_failed' => 'Telegram did not accept the test message. Check the bot token and chat id, and that you have sent /start to the bot.',
];
