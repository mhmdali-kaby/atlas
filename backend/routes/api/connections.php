<?php

declare(strict_types=1);

use App\Http\Controllers\Connections\ConnectionsController;
use Illuminate\Support\Facades\Route;

Route::prefix('connections')
    ->name('api.connections.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/', [ConnectionsController::class, 'index'])->name('index');
        Route::post('/', [ConnectionsController::class, 'store'])->name('store');
        Route::patch('/{connection}', [ConnectionsController::class, 'update'])->whereUuid('connection')->name('update');
        Route::delete('/{connection}', [ConnectionsController::class, 'destroy'])->whereUuid('connection')->name('destroy');
    });
