<?php

declare(strict_types=1);

use App\Http\Controllers\Warroom\WarroomController;
use Illuminate\Support\Facades\Route;

Route::prefix('warroom')
    ->name('api.warroom.')
    ->middleware('auth:sanctum')
    ->group(function (): void {
        Route::get('/agents', [WarroomController::class, 'agents'])->name('agents');
        Route::get('/threads', [WarroomController::class, 'threads'])->name('threads');
        Route::get('/messages', [WarroomController::class, 'messages'])->name('messages.index');
        Route::post('/messages', [WarroomController::class, 'store'])
            ->middleware('throttle:warroom-post')
            ->name('messages.store');
        Route::get('/replay', [WarroomController::class, 'replay'])->name('replay');
    });
