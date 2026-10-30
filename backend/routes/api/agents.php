<?php

declare(strict_types=1);

use App\Http\Controllers\Agents\AgentsController;
use Illuminate\Support\Facades\Route;

Route::prefix('agents')
    ->name('api.agents.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [AgentsController::class, 'index'])->name('index');
        Route::get('/claude-status', [AgentsController::class, 'claudeStatus'])
            ->middleware('throttle:claude-status')->name('claude-status');
    });
