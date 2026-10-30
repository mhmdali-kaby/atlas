<?php

declare(strict_types=1);

use App\Http\Controllers\Portfolio\HistoryController;
use Illuminate\Support\Facades\Route;

// ATL-172, brief §13 History, §16: fills and closed positions with filters, and their CSV export.
Route::middleware('auth:sanctum')
    ->name('api.portfolio.')
    ->group(function (): void {
        Route::get('/portfolio/history', [HistoryController::class, 'index'])->name('history');
        Route::get('/exports/{type}.csv', [HistoryController::class, 'export'])
            ->whereIn('type', ['fills', 'closed'])
            ->name('history.export');
    });
