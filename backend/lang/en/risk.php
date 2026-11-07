<?php

declare(strict_types=1);

// Manual risk check API messages (ATL-128). Keys match the `error` slug in the response.
return [
    'invalid_proposal' => 'The trading service refused this proposal as written. Check the stop, entry and take-profits.',
    'risk_engine_unavailable' => 'The Risk Engine cannot run yet. Make sure the risk settings are saved, then try again.',
    'trading_core_error' => 'The trading service could not carry out the risk check. Try again.',
    'trading_core_unavailable' => 'The trading service is not running. Start Atlas and try again.',
];
