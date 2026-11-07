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

    // ATL-255: `./atlas reset-two-factor` — for an owner who lost both their phone and their codes.
    'cli' => [
        'already_off' => 'Two-factor sign-in is already off for :email. Nothing to do.',
        'confirm' => 'Turn off two-factor sign-in for :email? This removes the authenticator and every recovery code.',
        'cancelled' => 'Nothing changed.',
        'done' => 'Two-factor sign-in is now off for :email. They can sign in with just their password.',
        'no_owner' => 'No owner exists yet.',
        'multiple_owners' => 'More than one owner exists; pass --email=<address> to say which one.',
        'no_such_owner' => 'No owner with email :email.',
    ],
];
