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

    // ATL-215: links in Telegram messages point at the local SPA (the owner opens them on the Mac).
    'telegram' => [
        'app_url' => env('FRONTEND_URL', 'http://localhost:3000'),
    ],

    // Decision #242: agents run on the owner's logged-in Claude Code CLI, not an API key.
    // `null` means atlas:doctor looks for `claude` on PATH; ATLAS_CLAUDE_BIN names an exact path
    // when the CLI isn't on PATH for the user running `./atlas up`.
    'claude_code' => [
        'bin' => env('ATLAS_CLAUDE_BIN'),
    ],

    // ATL-251: the agents' prompt files (services/agents/prompts), read only to tell which stored
    // prompt_versions row is the current file. Never served: the API returns stored bodies only.
    'agent_prompts' => [
        'dir' => env('ATLAS_AGENT_PROMPTS_DIR', base_path('../services/agents/prompts')),
    ],

    // ATL-231, decision #255 (3): the pg_dump `php artisan atlas:backup` runs. Its major version
    // must be at least the server's (PostgreSQL 16); Homebrew's libpq ships one
    // (/opt/homebrew/opt/libpq/bin/pg_dump).
    'pg_dump' => [
        'bin' => env('PG_DUMP_BIN', 'pg_dump'),
    ],

    'slack' => [
        'notifications' => [
            'bot_user_oauth_token' => env('SLACK_BOT_USER_OAUTH_TOKEN'),
            'channel' => env('SLACK_BOT_USER_DEFAULT_CHANNEL'),
        ],
    ],

    // ATL-262 (security round 20): PortfolioDemoSeeder, WarroomDemoSeeder, BackfillDemoSeeder and
    // ResearchDemoSeeder refuse to overwrite a database that already looks like a real install (a
    // verified connection, or any equity history) unless the operator sets ATLAS_DEMO_SEED=1,
    // their explicit statement that this install is a demo box, not the owner's.
    'demo_seed' => [
        'enabled' => env('ATLAS_DEMO_SEED') === '1',
    ],

];
