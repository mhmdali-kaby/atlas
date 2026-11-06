<?php

declare(strict_types=1);

// ATL-245: /api/me/two-factor — optional TOTP enrolment.
return [
    'fields' => [
        'password' => 'password',
        'recovery_code' => 'recovery code',
        'code' => 'code',
    ],
    'password_wrong' => 'The password is incorrect.',
    'code_wrong' => 'The code is incorrect or has already been used. Enter the current code from your authenticator app.',
    'not_started' => 'Start two-factor setup before entering a code.',
    'already_enabled' => 'Two-factor authentication is already on. Turn it off first to set it up again.',
    'not_enabled' => 'Two-factor authentication is off.',
    'challenge_expired' => 'Your sign-in has expired. Enter your email and password again.',
    'challenge_code_wrong' => 'The code is incorrect or has already been used. Enter the current code from your authenticator app, or a recovery code.',
];
