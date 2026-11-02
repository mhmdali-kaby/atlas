<?php

declare(strict_types=1);

// ATL-226: field names and messages for /api/me/preferences.
return [
    'fields' => [
        'name' => 'name',
        'locale' => 'language',
        'timezone' => 'time zone',
        'theme' => 'appearance',
    ],
    'timezone_invalid' => 'The time zone must be a valid IANA time zone, such as Asia/Baghdad.',
];
