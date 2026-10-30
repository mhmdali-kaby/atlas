<?php

declare(strict_types=1);

use App\Http\Controllers\Portfolio\BenchmarkController;
use Illuminate\Support\Facades\Route;

// ATL-176, brief §13 Overview, §16.
Route::middleware('auth:sanctum')
    ->name('api.portfolio.')
    ->group(function (): void {
        Route::get('/portfolio/benchmark', [BenchmarkController::class, 'show'])->name('benchmark');
    });
