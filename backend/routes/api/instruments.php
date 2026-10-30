<?php

declare(strict_types=1);

use App\Http\Controllers\Instruments\InstrumentsController;
use Illuminate\Support\Facades\Route;

// ATL-119: read-only instrument search for the owner's pin search (ATL-115) and any other
// instrument picker (brief §16 /api/instruments).
Route::middleware('auth:sanctum')
    ->name('api.instruments.')
    ->group(function (): void {
        Route::get('/instruments', [InstrumentsController::class, 'index'])->name('index');
    });
