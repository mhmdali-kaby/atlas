<?php

declare(strict_types=1);

use Illuminate\Support\Facades\Route;

/*
 * Every API feature owns one file in routes/api/ (e.g. routes/api/connections.php).
 * They are loaded here in alphabetical order under the /api prefix, so two features never
 * edit the same routes file. Authenticated routes wrap themselves in `auth:sanctum`.
 */
foreach (glob(__DIR__.'/api/*.php') ?: [] as $routeFile) {
    require $routeFile;
}

Route::get('/ping', fn () => response()->json(['ok' => true, 'service' => 'atlas-api']))
    ->name('api.ping');
