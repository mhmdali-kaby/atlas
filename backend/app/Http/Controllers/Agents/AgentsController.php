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
     */
    public function claudeStatus(Doctor $doctor): JsonResponse
    {
        $checks = [];
        foreach ($doctor->claudeChecks() as $check) {
            $checks[$check->name] = $check;
        }

        $passed = static fn (string $name): bool => ($checks[$name] ?? null)?->status === CheckResult::PASS;

        return response()->json(['data' => [
            'ok' => $passed('claude_cli') && $passed('claude_cli_login') && $passed('claude_api_key'),
            'installed' => $passed('claude_cli'),
            'logged_in' => $passed('claude_cli_login'),
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
        ]]);
    }
}
