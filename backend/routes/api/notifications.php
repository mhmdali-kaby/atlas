<?php

declare(strict_types=1);

use App\Http\Controllers\Notifications\NotificationController;
use Illuminate\Support\Facades\Route;

// ATL-214, brief §17: the owner's notifications of the active env (stored by atlas:bridge).
Route::prefix('notifications')
    ->name('api.notifications.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [NotificationController::class, 'index'])->name('index');

        Route::middleware('throttle:notifications-write')->group(function (): void {
            // Declared before /{notification}/read so the literal segment is never read as an id.
            Route::post('/read-all', [NotificationController::class, 'readAll'])->name('read-all');
            Route::post('/{notification}/read', [NotificationController::class, 'read'])->whereUuid('notification')->name('read');
        });
    });
