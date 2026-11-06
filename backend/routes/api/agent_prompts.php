<?php

declare(strict_types=1);

use App\Http\Controllers\Agents\AgentPromptsController;
use Illuminate\Support\Facades\Route;

// ATL-251, brief §14.7, decision #265 (3): prompt versions, bodies and the pin.
Route::prefix('agents/{slug}')
    ->name('api.agents.prompts.')
    ->middleware('auth:sanctum')
    ->where(['slug' => '[a-z_]+', 'version' => '[0-9]+'])
    ->group(function (): void {
        Route::get('/prompts', [AgentPromptsController::class, 'index'])->name('index');
        Route::get('/prompts/{version}', [AgentPromptsController::class, 'show'])->name('show');
        Route::put('/prompt-pin', [AgentPromptsController::class, 'pin'])->name('pin');
    });
