<?php

declare(strict_types=1);

use App\Http\Controllers\Cycles\CycleController;
use Illuminate\Support\Facades\Route;

// ATL-163: the composer's /cycle now asks the Orchestrator for a manual cycle (brief §12).
Route::prefix('cycles')
    ->name('api.cycles.')
    ->middleware(['auth:sanctum', 'throttle:cycles-start'])
    ->group(function (): void {
        Route::post('/', [CycleController::class, 'store'])->name('store');
    });
