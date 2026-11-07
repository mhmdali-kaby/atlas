<?php

declare(strict_types=1);

use App\Http\Controllers\System\SystemController;
use Illuminate\Support\Facades\Route;

// ATL-148: system health and the kill switch (brief §10, §16). Pause, resume and kill skip the
// shared api limiter and use their own (system-control), so a flood of other requests can never
// make the kill switch answer 429.
Route::prefix('system')
    ->name('api.system.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/health', [SystemController::class, 'health'])->name('health');

        Route::withoutMiddleware('throttle:api')
            ->middleware('throttle:system-control')
            ->group(function (): void {
                Route::post('/pause', [SystemController::class, 'pause'])->name('pause');
                Route::post('/resume', [SystemController::class, 'resume'])->name('resume');
                Route::post('/kill', [SystemController::class, 'kill'])->name('kill');
            });
    });
