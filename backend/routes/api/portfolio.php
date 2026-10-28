<?php

declare(strict_types=1);

use App\Http\Controllers\Portfolio\PortfolioController;
use Illuminate\Support\Facades\Route;

Route::prefix('portfolio')
    ->name('api.portfolio.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/summary', [PortfolioController::class, 'summary'])->name('summary');
        Route::get('/equity', [PortfolioController::class, 'equity'])->name('equity');
        Route::get('/holdings', [PortfolioController::class, 'holdings'])->name('holdings');
        Route::get('/positions', [PortfolioController::class, 'positions'])->name('positions');
        Route::get('/cashflows', [PortfolioController::class, 'cashflows'])->name('cashflows');
    });
