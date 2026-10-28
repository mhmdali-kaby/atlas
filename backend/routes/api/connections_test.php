<?php

declare(strict_types=1);

use App\Http\Controllers\Connections\ConnectionTestController;
use Illuminate\Support\Facades\Route;

Route::post('connections/{connection}/test', ConnectionTestController::class)
    ->middleware('auth:sanctum')
    ->whereUuid('connection')
    ->name('api.connections.test');
