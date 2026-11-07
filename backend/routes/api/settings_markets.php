<?php

declare(strict_types=1);

use App\Http\Controllers\Settings\MarketsEligibilityController;
use Illuminate\Support\Facades\Route;

/** ATL-155, decision #250 (1): which Settings -> Markets filter each instrument currently fails. */
Route::prefix('settings/markets')
    ->name('api.settings.markets.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/eligibility', MarketsEligibilityController::class)->name('eligibility');
    });
