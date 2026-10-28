<?php

declare(strict_types=1);

use App\Http\Controllers\Settings\SettingsController;
use Illuminate\Support\Facades\Route;

Route::prefix('settings')
    ->name('api.settings.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [SettingsController::class, 'show'])->name('show');
        Route::patch('/', [SettingsController::class, 'update'])->name('update');
        Route::get('/history', [SettingsController::class, 'history'])->name('history');
    });
