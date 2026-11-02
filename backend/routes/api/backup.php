<?php

declare(strict_types=1);

use App\Http\Controllers\Backup\BackupController;
use Illuminate\Support\Facades\Route;

// ATL-231, decision #255 (3): Settings → Backup. The settings themselves are edited through
// PATCH /api/settings (backup.enabled, backup.folder, backup.time, backup.keep).
Route::prefix('backup')
    ->name('api.backup.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [BackupController::class, 'show'])->name('show');
        Route::post('/run', [BackupController::class, 'run'])->middleware('throttle:backup-run')->name('run');
    });
