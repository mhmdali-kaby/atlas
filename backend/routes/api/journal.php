<?php

declare(strict_types=1);

use App\Http\Controllers\Journal\JournalController;
use Illuminate\Support\Facades\Route;

// ATL-200, brief §16: the agents' journal of the active env (read-only).
Route::prefix('journal')
    ->name('api.journal.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [JournalController::class, 'index'])->name('index');
        // Declared before /{entry} so the literal segment is never read as an id.
        Route::get('/weekly-reviews', [JournalController::class, 'weeklyReviews'])->name('weekly-reviews');
        Route::get('/{entry}', [JournalController::class, 'show'])->whereUuid('entry')->name('show');
    });
