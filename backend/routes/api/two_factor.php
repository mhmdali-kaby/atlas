<?php

declare(strict_types=1);

use App\Http\Controllers\TwoFactor\TwoFactorController;
use Illuminate\Support\Facades\Route;

// ATL-245, decision #265 (1): optional TOTP enrolment for the signed-in owner.
Route::prefix('me/two-factor')
    ->name('api.me.two-factor.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [TwoFactorController::class, 'status'])->middleware('throttle:60,1')->name('status');
        Route::middleware('throttle:two-factor')->group(function (): void {
            Route::post('/start', [TwoFactorController::class, 'start'])->name('start');
            Route::post('/confirm', [TwoFactorController::class, 'confirm'])->name('confirm');
            Route::post('/recovery-codes', [TwoFactorController::class, 'regenerate'])->name('recovery-codes');
            Route::delete('/', [TwoFactorController::class, 'disable'])->name('disable');
        });
    });
