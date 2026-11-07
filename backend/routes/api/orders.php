<?php

declare(strict_types=1);

use App\Http\Controllers\System\TradeActionController;
use Illuminate\Support\Facades\Route;

// ATL-148: the owner cancels one order of the active env (brief §13 Orders, §16).
Route::prefix('orders')
    ->name('api.orders.')
    ->middleware(['auth:sanctum', 'throttle:trades-write'])
    ->group(function (): void {
        Route::post('/{order}/cancel', [TradeActionController::class, 'cancelOrder'])
            ->whereUuid('order')
            ->name('cancel');
    });
