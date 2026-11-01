<?php

declare(strict_types=1);

use App\Http\Controllers\Notifications\TelegramController;
use Illuminate\Support\Facades\Route;

// ATL-215, brief §14.9: the owner's Telegram bot. Values are never returned (last 4 characters only).
Route::prefix('notifications/telegram')
    ->name('api.notifications.telegram.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [TelegramController::class, 'show'])->name('show');

        Route::middleware('throttle:telegram-write')->group(function (): void {
            Route::put('/', [TelegramController::class, 'update'])->name('update');
            Route::delete('/', [TelegramController::class, 'destroy'])->name('destroy');
            Route::post('/test', [TelegramController::class, 'test'])->name('test');
        });
    });
