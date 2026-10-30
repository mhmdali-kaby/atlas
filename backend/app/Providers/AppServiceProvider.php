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

        // ATL-128: owner manual risk checks, per user. Each writes a proposal and a risk check.
        RateLimiter::for('risk-check', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.risk_api.checks_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 20)
                ->by('risk-check|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

        // ATL-103: owner watchlist pin/remove/restore, per user.
        RateLimiter::for('watchlists-write', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.watchlists_api.writes_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 30)
                ->by('watchlists-write|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

        // ATL-66: Settings → Data sources writes (toggle, poll interval, set/remove key), per user.
        RateLimiter::for('data-sources-write', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.data_sources_api.writes_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 20)
                ->by('data-sources-write|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

        // ATL-122 (security round 11, finding 2): GET /api/agents/claude-status spawns the
        // claude CLI; its result is cached, but this still bounds how often a miss can run it.
        RateLimiter::for('claude-status', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.agents_api.claude_status_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 6)
                ->by('claude-status|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });
    }
}
