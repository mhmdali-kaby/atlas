<?php

declare(strict_types=1);

use App\Http\Controllers\Portfolio\PerformanceController;
use Illuminate\Support\Facades\Route;

// ATL-174, brief §13 Performance, §16.
Route::middleware('auth:sanctum')
    ->name('api.portfolio.')
    ->group(function (): void {
        Route::get('/portfolio/performance', [PerformanceController::class, 'show'])->name('performance');
    });
