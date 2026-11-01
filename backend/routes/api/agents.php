<?php

declare(strict_types=1);

use App\Http\Controllers\Agents\AgentsController;
use App\Http\Controllers\Journal\AgentScoresController;
use Illuminate\Support\Facades\Route;

Route::prefix('agents')
    ->name('api.agents.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [AgentsController::class, 'index'])->name('index');
        Route::get('/claude-status', [AgentsController::class, 'claudeStatus'])
            ->middleware('throttle:claude-status')->name('claude-status');
        // ATL-200: daily scores per agent and the shared notebook (read-only).
        Route::get('/scores', [AgentScoresController::class, 'scores'])->name('scores');
        Route::get('/notebook', [AgentScoresController::class, 'notebook'])->name('notebook');
    });
