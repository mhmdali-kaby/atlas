<?php

declare(strict_types=1);

namespace App\Providers;

use Illuminate\Cache\RateLimiting\Limit;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\ServiceProvider;

class AppServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        //
    }

    public function boot(): void
    {
        // Owner login: 5 attempts per minute per client IP; the 6th gets 429.
        RateLimiter::for('login', fn (Request $request): Limit => Limit::perMinute(5)->by('login|'.$request->ip()));
    }
}
