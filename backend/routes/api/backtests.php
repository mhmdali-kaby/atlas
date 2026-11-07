<?php

declare(strict_types=1);

use App\Http\Controllers\Backtests\BacktestController;
use Illuminate\Support\Facades\Route;

// ATL-208: owner backtests (decision #253 L). Research only; trading_core's /internal/backtest is read-only.
Route::prefix('backtests')
    ->name('api.backtests.')
    ->middleware('auth:sanctum')
    ->whereUuid('backtest')
    ->group(function (): void {
        Route::get('/', [BacktestController::class, 'index'])->name('index');
        Route::get('/{backtest}', [BacktestController::class, 'show'])->name('show');
        Route::post('/', [BacktestController::class, 'store'])
            ->middleware('throttle:backtests-start')
            ->name('store');
    });
