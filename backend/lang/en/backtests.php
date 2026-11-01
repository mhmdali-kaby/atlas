<?php

declare(strict_types=1);

// Backtests API messages (ATL-208). Validation messages, then failure reasons keyed by `error`.
return [
    'unknown_field' => 'The field :field is not part of a backtest request.',
    'close_reserved' => '"close" is reserved for the close price and cannot name an indicator.',
    'bad_indicator_name' => 'The indicator name :name must start with a letter or underscore and use only letters, digits and underscores.',
    'unknown_indicator' => 'The condition uses an indicator that is not defined: :name.',
    'bad_right' => 'The right side of a condition must be an indicator name or a number.',
    'period_order' => 'The period must end after it starts.',
    'trading_core_error' => 'The trading service could not run the backtest. Try again.',
    'trading_core_unavailable' => 'The trading service is not running. Start Atlas and try again.',
    'job_failed' => 'The backtest job stopped before it finished. Try a shorter period or fewer instruments.',
];
