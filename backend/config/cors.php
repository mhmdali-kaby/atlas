<?php

return [

    /*
    |--------------------------------------------------------------------------
    | Cross-Origin Resource Sharing (CORS) Configuration
    |--------------------------------------------------------------------------
    |
    | Here you may configure your settings for cross-origin resource sharing
    | or "CORS". This determines what cross-origin operations may execute
    | in web browsers. You are free to adjust these settings as needed.
    |
    | To learn more: https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS
    |
    */

    'paths' => ['api/*', 'sanctum/csrf-cookie', 'broadcasting/auth'],

    'allowed_methods' => ['*'],

    // FRONTEND_URL is matched as an exact-origin pattern rather than listed in allowed_origins:
    // with a single allowed origin, php-cors echoes it to every caller. As a pattern, only a
    // request whose Origin is FRONTEND_URL gets Access-Control-Allow-Origin (plus Vary: Origin).
    'allowed_origins' => [],

    'allowed_origins_patterns' => [
        '#^'.preg_quote(rtrim((string) env('FRONTEND_URL', 'http://localhost:3000'), '/'), '#').'$#',
    ],

    'allowed_headers' => ['*'],

    'exposed_headers' => [],

    'max_age' => 0,

    'supports_credentials' => true,

];
