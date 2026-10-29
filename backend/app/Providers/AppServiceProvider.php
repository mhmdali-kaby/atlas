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
        // ATL-33 AC3: stops PHP capturing function arguments (ciphertext, secrets) in exception
        // traces in the first place. Must run before any request code can throw, so it is set
        // here rather than in boot(). DropExceptionTraceArgsProcessor (config/logging.php 'tap')
        // is the second line of defence for an exception already carrying them.
        ini_set('zend.exception_ignore_args', '1');
    }

    public function boot(): void
    {
        // Owner login: 5 attempts per minute per client IP; the 6th gets 429.
        RateLimiter::for('login', fn (Request $request): Limit => Limit::perMinute(5)->by('login|'.$request->ip()));

        // ATL-38: owner War Room posts, per user (brief §16).
        RateLimiter::for('warroom-post', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.warroom.post_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 30)
                ->by('warroom-post|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

        // ATL-48: Settings → Data re-sync and pause, per user. Each starts work in trading_core.
        foreach (['data-resync' => 'resync_per_minute', 'data-pause' => 'pause_per_minute'] as $name => $key) {
            RateLimiter::for($name, function (Request $request) use ($name, $key): Limit {
                $perMinute = config("atlas_defaults.data_api.{$key}");
                $user = $request->user();

                return Limit::perMinute(is_int($perMinute) ? $perMinute : 1)
                    ->by($name.'|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
            });
        }
    }
}
