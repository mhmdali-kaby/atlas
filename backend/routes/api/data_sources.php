<?php

declare(strict_types=1);

use App\Http\Controllers\DataSources\DataSourcesController;
use Illuminate\Support\Facades\Route;

// ATL-66: Settings → Data sources (brief §7.2, §14.10; decision #243).
Route::prefix('data-sources')
    ->name('api.data_sources.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [DataSourcesController::class, 'index'])->name('index');

        Route::middleware('throttle:data-sources-write')
            ->where(['source' => '[a-z0-9][a-z0-9._-]{0,63}'])
            ->group(function (): void {
                Route::patch('/{source:key}', [DataSourcesController::class, 'update'])->name('update');
                Route::put('/{source:key}/key', [DataSourcesController::class, 'setKey'])->name('key.set');
                Route::delete('/{source:key}/key', [DataSourcesController::class, 'removeKey'])->name('key.remove');
            });
    });
