<?php

declare(strict_types=1);

use App\Http\Controllers\Environment\EnvironmentController;
use Illuminate\Support\Facades\Route;

// ATL-236, decision #255 (4)(5): the stored active env, the live pre-flight and the guarded switch.
// The switch has its own limiter (environment-switch, 5/min per user) for password guessing.
Route::prefix('environment')
    ->name('api.environment.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [EnvironmentController::class, 'show'])->name('show');
        Route::put('/attestations', [EnvironmentController::class, 'attestations'])->middleware('throttle:60,1')->name('attestations');
        Route::post('/switch', [EnvironmentController::class, 'switch'])->middleware('throttle:environment-switch')->name('switch');
    });
