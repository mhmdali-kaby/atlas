<?php

declare(strict_types=1);

namespace App\Http\Controllers\System;

use App\Http\Controllers\Controller;
use App\Http\Requests\System\KillRequest;
use App\Http\Requests\System\PauseRequest;
use App\Http\Requests\System\ResumeRequest;
use App\Support\Auditable;
use App\Support\System\ActiveEnv;
use App\Support\System\ControlCall;
use App\Support\System\SystemHealth;
use Carbon\CarbonImmutable;
use Illuminate\Http\JsonResponse;

/**
 * /api/system (brief §10, §16; ATL-148). Health reads Postgres and Redis only. Pause, resume and
 * kill make one signed call each to trading_core (ATL-147), always write one audit_log row (even
 * when trading_core is down) and return trading_core's answer. These three routes have their own
 * rate limiter and skip the shared api limiter (routes/api/system.php).
 */
class SystemController extends Controller
{
    public function __construct(private readonly Auditable $audit) {}

    public function health(SystemHealth $health): JsonResponse
    {
        return response()->json(['data' => $health->build(ActiveEnv::get(), CarbonImmutable::now())]);
    }

    public function pause(PauseRequest $request): JsonResponse
    {
        $payload = $request->payload();

        return $this->forward('system.paused', '/internal/system/pause', $payload, $payload);
    }

    public function resume(ResumeRequest $request): JsonResponse
    {
        return $this->forward('system.resumed', '/internal/system/resume', [], []);
    }

    public function kill(KillRequest $request): JsonResponse
    {
        $flatten = $request->flatten();

        return $this->forward('system.killed', '/internal/system/kill', ['flatten' => $flatten], ['flatten' => $flatten]);
    }

    /**
     * @param  array<string, mixed>  $body
     * @param  array<string, mixed>  $requested
     */
    private function forward(string $action, string $path, array $body, array $requested): JsonResponse
    {
        $env = ActiveEnv::get();
        $call = ControlCall::post($path, $body);
        $this->audit->record($action, 'trading_state', $env, ['env' => $env, ...$requested], $call->auditOutcome());

        return response()->json($call->body, $call->status);
    }
}
