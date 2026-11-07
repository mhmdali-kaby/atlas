<?php

declare(strict_types=1);

// Short human messages for Test connection error slugs (ATL-8). Keys match trading_core's `error`.
return [
    'withdraw_permission_enabled' => 'This key can withdraw funds. Create a key with Read + Trade only.',
    'invalid_credentials' => 'Key or passphrase is wrong',
    'wrong_environment' => 'This key belongs to the other environment (demo/live)',
    'ip_not_allowed' => 'This key only works from the IP addresses on its allow-list',
    'clock_out_of_sync' => 'This Mac\'s clock is out of sync with OKX',
    'key_frozen' => 'This key is frozen on OKX',
    'key_not_found' => 'OKX does not know this key',
    'decrypt_failed' => 'The saved key cannot be decrypted. Enter it again.',
    'okx_unreachable' => 'OKX could not be reached. Try again.',
    'okx_error' => 'OKX refused the request',
    'connection_not_found' => 'This connection no longer exists',
    'trading_core_unavailable' => 'The trading service is not running. Start Atlas and try again.',
    'trading_core_error' => 'The trading service could not run the test',
];
