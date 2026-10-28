<?php

declare(strict_types=1);

use App\Http\Controllers\Auth\AuthController;
use Illuminate\Support\Facades\Route;

Route::prefix('auth')->name('api.auth.')->group(function (): void {
    // Guest: lets the login screen show "Run ./atlas create-owner" on first run.
    Route::get('/status', [AuthController::class, 'status'])
        ->middleware('throttle:60,1')
        ->name('status');

    Route::post('/login', [AuthController::class, 'login'])
        ->middleware('throttle:login')
        ->name('login');

    Route::middleware('auth:sanctum')->group(function (): void {
        Route::post('/logout', [AuthController::class, 'logout'])->name('logout');
        Route::get('/me', [AuthController::class, 'me'])->name('me');
    });
});
