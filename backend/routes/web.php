<?php

declare(strict_types=1);

use Illuminate\Broadcasting\BroadcastController;
use Illuminate\Support\Facades\Route;

// The UI is the React SPA in frontend/. Laravel serves only the JSON API (routes/api.php),
// the Sanctum CSRF cookie route, and the routes below.

// routes/channels.php registers Broadcast::channel() authorizers (no HTTP route of its own).
require __DIR__.'/channels.php';

// security-audit-round-4 #4: Laravel's own Broadcast::routes() always exempts /broadcasting/auth
// from CSRF (BroadcastManager::routes() calls ->withoutMiddleware([PreventRequestForgery::class])
// unconditionally), which is wrong for a cookie-session SPA. Register it ourselves, inside this
// file's "web" group (session + CSRF), so a forged cross-site POST without X-XSRF-TOKEN gets 419
// like every other session route, instead of a free pass to read channel auth signatures.
//
// security-audit-round-5 #ATL-56: POST only. Accepting GET here (as Laravel's own
// Broadcast::routes() does) let a cross-site GET read a channel's auth signature without any
// CSRF token at all, since GET requests never carry one. Pusher-protocol channel auth is always
// a POST from Echo/Pusher-js; there is no legitimate GET caller to support.
Route::post('/broadcasting/auth', [BroadcastController::class, 'authenticate'])
    ->name('broadcasting.auth');
