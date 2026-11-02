<?php

declare(strict_types=1);

use App\Http\Controllers\Preferences\PreferencesController;
use Illuminate\Support\Facades\Route;

// ATL-226, decision #255 (1): the owner's name, language, display timezone and theme.
Route::prefix('me/preferences')
    ->name('api.me.preferences.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [PreferencesController::class, 'show'])->name('show');
        Route::patch('/', [PreferencesController::class, 'update'])->middleware('throttle:60,1')->name('update');
    });
