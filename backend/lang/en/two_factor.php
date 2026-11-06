<?php

declare(strict_types=1);

// ATL-245: /api/me/two-factor — optional TOTP enrolment.
return [
    'fields' => [
        'password' => 'password',
        'code' => 'code',
    ],
    'password_wrong' => 'The password is incorrect.',
    'code_wrong' => 'The code is incorrect or has already been used. Enter the current code from your authenticator app.',
    'not_started' => 'Start two-factor setup before entering a code.',
    'already_enabled' => 'Two-factor authentication is already on. Turn it off first to set it up again.',
    'not_enabled' => 'Two-factor authentication is off.',
];
