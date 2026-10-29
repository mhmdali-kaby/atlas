<?php

declare(strict_types=1);

use App\Http\Controllers\Data\DataController;
use Illuminate\Support\Facades\Route;

// ATL-48: Settings → Data (brief §14.10, §16).
Route::prefix('data')
    ->name('api.data.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/backfill', [DataController::class, 'backfill'])->name('backfill');
        Route::get('/storage', [DataController::class, 'storage'])->name('storage');
        Route::post('/resync', [DataController::class, 'resync'])
            ->middleware('throttle:data-resync')
            ->name('resync');
        Route::post('/pause', [DataController::class, 'pause'])
            ->middleware('throttle:data-pause')
            ->name('pause');
    });
