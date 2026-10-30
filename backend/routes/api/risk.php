<?php

declare(strict_types=1);

use App\Http\Controllers\Risk\RiskCheckController;
use Illuminate\Support\Facades\Route;

// ATL-128: the owner's manual risk check (decision #249 F). Relayed to trading_core; nothing reaches OKX.
Route::post('risk/check', [RiskCheckController::class, 'store'])
    ->middleware(['auth:sanctum', 'throttle:risk-check'])
    ->name('api.risk.check');
