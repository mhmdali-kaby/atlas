<?php

declare(strict_types=1);

use App\Http\Controllers\Settings\RiskPreviewController;
use Illuminate\Support\Facades\Route;

Route::get('settings/risk-preview', [RiskPreviewController::class, 'show'])
    ->middleware('auth:sanctum')
    ->name('api.settings.risk-preview');
