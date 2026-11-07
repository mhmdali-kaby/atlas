<?php

declare(strict_types=1);

namespace App\Http\Controllers\Agents;

use App\Http\Controllers\Controller;
use App\Http\Requests\Settings\SettingsEnvRequest;
use App\Support\Agents\AgentRoster;
use App\Support\Doctor\CheckResult;
use App\Support\Doctor\Doctor;
use Carbon\CarbonImmutable;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\Cache;

/**
 * ATL-113, brief §14.7/§16: the Agents page's read API. Agent settings are written through
 * PATCH /api/settings (agents.<slug>.* keys), never here.
 */
class AgentsController extends Controller
{
    /** Anything shaped like an email address is masked out of CLI-derived text. */
    private const EMAIL_PATTERN = '/[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}/i';

    public function index(SettingsEnvRequest $request, AgentRoster $roster): JsonResponse
    {
        return response()->json($roster->list($request->env()));
    }

    /**
     * Decision #242: the Doctor's three `claude` CLI checks. Only check names, statuses and the
     * Doctor's own translated detail lines are returned: never an API key value (the key check
     * reports variable names only) and never the account email (scrubbed as a second guard).
     *
     * ATL-122 (security round 11, finding 2): spawning the `claude` CLI on every request could
     * fall back to a paid model turn (see Doctor::claudeChecks()). The checks are cached for
     * `agents_api.claude_status_cache_seconds` (default 60s) so a burst of calls shares one run,
     * and the route carries `throttle:claude-status` so repeated misses cannot bypass that.
     * `logged_in` is `null` ("unknown") when the login check itself is unknown, not a pass/fail.
     */
    public function claudeStatus(Doctor $doctor): JsonResponse
    {
        $seconds = config('atlas_defaults.agents_api.claude_status_cache_seconds');
        $seconds = is_int($seconds) ? $seconds : 60;

        $data = Cache::remember('agents.claude-status', $seconds, function () use ($doctor): array {
            $checks = [];
            foreach ($doctor->claudeChecks() as $check) {
                $checks[$check->name] = $check;
            }

            $passed = static fn (string $name): bool => ($checks[$name] ?? null)?->status === CheckResult::PASS;
            $loginStatus = ($checks['claude_cli_login'] ?? null)?->status;
            $loggedIn = match ($loginStatus) {
                CheckResult::PASS => true,
                CheckResult::FAIL => false,
                default => null,
            };

            return [
                'ok' => $passed('claude_cli') && $loggedIn === true && $passed('claude_api_key'),
                'installed' => $passed('claude_cli'),
                'logged_in' => $loggedIn,
                'api_key_env_clear' => $passed('claude_api_key'),
                'login_command' => 'claude login',
                'checks' => array_values(array_map(
                    static fn (CheckResult $check): array => [
                        ...$check->toArray(),
                        'detail' => (string) preg_replace(self::EMAIL_PATTERN, '[hidden]', $check->detail),
                    ],
                    $checks,
                )),
                'checked_at' => CarbonImmutable::now()->utc()->toIso8601ZuluString('microsecond'),
            ];
        });

        return response()->json(['data' => $data]);
    }
}
