<?php

return [

    /*
    |--------------------------------------------------------------------------
    | Third Party Services
    |--------------------------------------------------------------------------
    |
    | This file is for storing the credentials for third party services such
    | as Resend, Postmark, AWS, and more. This file provides the de facto
    | location for this type of information, allowing packages to have
    | a conventional file to locate the various service credentials.
    |
    */

    'postmark' => [
        'key' => env('POSTMARK_API_KEY'),
    ],

    'resend' => [
        'key' => env('RESEND_API_KEY'),
    ],

    'ses' => [
        'key' => env('AWS_ACCESS_KEY_ID'),
        'secret' => env('AWS_SECRET_ACCESS_KEY'),
        'region' => env('AWS_DEFAULT_REGION', 'us-east-1'),
    ],

    // Internal FastAPI trading core (decision #235). Calls are HMAC-signed by App\Support\TradingCore.
    'trading_core' => [
        'url' => env('TRADING_CORE_URL', 'http://127.0.0.1:8100'),
        'secret' => env('INTERNAL_SHARED_SECRET', ''),
        'timeout' => 10,
    ],

    // Decision #242: agents run on the owner's logged-in Claude Code CLI, not an API key.
    // `null` means atlas:doctor looks for `claude` on PATH; ATLAS_CLAUDE_BIN names an exact path
    // when the CLI isn't on PATH for the user running `./atlas up`.
    'claude_code' => [
        'bin' => env('ATLAS_CLAUDE_BIN'),
    ],

    'slack' => [
        'notifications' => [
            'bot_user_oauth_token' => env('SLACK_BOT_USER_OAUTH_TOKEN'),
            'channel' => env('SLACK_BOT_USER_DEFAULT_CHANNEL'),
        ],
    ],

];
