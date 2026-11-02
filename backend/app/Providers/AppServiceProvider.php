<?php

declare(strict_types=1);

namespace App\Providers;

use App\Models\User;
use Illuminate\Cache\RateLimiting\Limit;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Gate;
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
        // ATL-148: pause / resume / kill, and closing a position. Atlas has a single owner (brief §5,
        // multi-user ready): any signed-in user is that owner. Agents never come through here.
        Gate::define('control-trading', fn (User $user): bool => true);
        Gate::define('close-position', fn (User $user): bool => true);

        // Owner login: 5 attempts per minute per client IP; the 6th gets 429.
        RateLimiter::for('login', fn (Request $request): Limit => Limit::perMinute(5)->by('login|'.$request->ip()));

        // ATL-236: the live switch checks the owner password, so it gets its own tight limiter.
        RateLimiter::for('environment-switch', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.env.live_preflight.switch_attempts_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 5)
                ->by('environment-switch|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

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

        // ATL-208: owner backtest starts, per user. Each run holds a worker for up to a minute.
        RateLimiter::for('backtests-start', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.backtests_api.starts_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 6)
                ->by('backtests-start|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

        // ATL-231: "Back up now". One for the whole app, not per user: every run dumps the same
        // database into the same folder.
        RateLimiter::for('backup-run', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.backup.runs_per_minute');

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 1)->by('backup-run');
        });

        // ATL-103: owner watchlist pin/remove/restore, per user.
        RateLimiter::for('notifications-write', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.notifications.writes_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 120)
                ->by('notifications-write|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

        // ATL-215: the Telegram bot settings and its test message, per user.
        RateLimiter::for('telegram-write', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.telegram.writes_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 30)
                ->by('telegram-write|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

        RateLimiter::for('watchlists-write', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.watchlists_api.writes_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 30)
                ->by('watchlists-write|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

        // ATL-163: /cycle now, per user. One manual cycle request a minute.
        RateLimiter::for('cycles-start', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.cycles_api.start_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 1)
                ->by('cycles-start|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

        // ATL-162: owner approve / reject / edit / ask-team on proposals, per user.
        RateLimiter::for('proposals-write', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.proposals_api.writes_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 30)
                ->by('proposals-write|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
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

        // ATL-148: the shared limiter on every /api route (bootstrap/app.php throttleApi). The kill
        // switch, pause and resume opt out of it (routes/api/system.php) and use system-control
        // instead, so a flood of other requests can never make the kill switch answer 429.
        RateLimiter::for('api', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.system_api.api_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 1200)
                ->by('api|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

        // ATL-148: kill switch, pause and resume, per user, on a counter of their own.
        RateLimiter::for('system-control', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.system_api.control_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 30)
                ->by('system-control|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });

        // ATL-148: owner order cancel and position close, per user.
        RateLimiter::for('trades-write', function (Request $request): Limit {
            $perMinute = config('atlas_defaults.system_api.trades_per_minute');
            $user = $request->user();

            return Limit::perMinute(is_int($perMinute) ? $perMinute : 30)
                ->by('trades-write|'.($user !== null ? (string) $user->getAuthIdentifier() : $request->ip()));
        });
    }
}
