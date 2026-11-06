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

    // ATL-256: without this the SPA's fetch() cannot read Retry-After on a cross-origin 429
    // (browsers only expose a small safelist of response headers by default), so every "try
    // again in N s" wait line fell back to a hard-coded 60s instead of the server's real value.
    'exposed_headers' => ['Retry-After'],

    'max_age' => 0,

    'supports_credentials' => true,

];
