<?php

declare(strict_types=1);

use App\Http\Controllers\Watchlists\WatchlistController;
use Illuminate\Support\Facades\Route;

// ATL-103: the owner's view of today's watchlist and pin/remove/restore during the edit window
// (brief §8, decision #247 (3)).
Route::prefix('watchlists')
    ->name('api.watchlists.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [WatchlistController::class, 'show'])->name('show');

        Route::middleware('throttle:watchlists-write')
            ->whereUuid(['watchlist', 'item'])
            ->scopeBindings()
            ->group(function (): void {
                Route::post('/{watchlist}/items/{item}/remove', [WatchlistController::class, 'remove'])->name('items.remove');
                Route::post('/{watchlist}/items/{item}/restore', [WatchlistController::class, 'restore'])->name('items.restore');
                Route::post('/{watchlist}/pin', [WatchlistController::class, 'pin'])->name('pin');
            });
    });
