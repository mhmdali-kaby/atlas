<?php

declare(strict_types=1);

// ATL-148: /api/system, order cancel and position close.
return [
    'trading_core_unavailable' => 'The trading service is not running, so nothing was sent to OKX. Start Atlas and try again.',
    'trading_core_error' => 'The trading service gave an unexpected answer. Check the War Room and try again.',
    'confirm_kill' => 'Send confirm "KILL" to use the kill switch.',
    'confirm_close' => 'Confirm closing this position.',
    'order_not_found' => 'There is no such order in this environment.',
    'position_not_found' => 'There is no such position in this environment.',
];
