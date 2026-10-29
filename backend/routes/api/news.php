<?php

declare(strict_types=1);

use App\Http\Controllers\News\NewsController;
use Illuminate\Support\Facades\Route;

// ATL-67: read-only news search, story clusters, the economic/earnings calendar and the
// latest market sentiment (brief §16).
Route::middleware('auth:sanctum')
    ->name('api.news.')
    ->group(function (): void {
        Route::get('/news', [NewsController::class, 'index'])->name('index');
        Route::get('/news/clusters/{cluster}', [NewsController::class, 'cluster'])
            ->whereUuid('cluster')
            ->name('clusters.show');
        Route::get('/calendar', [NewsController::class, 'calendar'])->name('calendar');
        Route::get('/sentiment/latest', [NewsController::class, 'sentiment'])->name('sentiment.latest');
    });
