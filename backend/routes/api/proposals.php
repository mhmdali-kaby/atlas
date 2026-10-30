<?php

declare(strict_types=1);

use App\Http\Controllers\Proposals\ProposalController;
use Illuminate\Support\Facades\Route;

// ATL-162: proposals and the owner's approve / reject / edit / ask-team (brief §12, §16).
Route::prefix('proposals')
    ->name('api.proposals.')
    ->middleware('auth:sanctum')
    ->whereUuid('proposal')
    ->group(function (): void {
        Route::get('/', [ProposalController::class, 'index'])->name('index');
        Route::get('/{proposal}', [ProposalController::class, 'show'])->name('show');

        Route::middleware('throttle:proposals-write')->group(function (): void {
            Route::post('/{proposal}/approve', [ProposalController::class, 'approve'])->name('approve');
            Route::post('/{proposal}/reject', [ProposalController::class, 'reject'])->name('reject');
            Route::patch('/{proposal}', [ProposalController::class, 'update'])->name('update');
            Route::post('/{proposal}/ask-team', [ProposalController::class, 'askTeam'])->name('ask-team');
        });
    });
