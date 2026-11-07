<?php

declare(strict_types=1);

namespace App\Http\Controllers\Cycles;

use App\Http\Controllers\Controller;
use App\Support\Auditable;
use App\Support\Events\EventBus;
use App\Support\System\ActiveEnv;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Str;

/**
 * POST /api/cycles (ATL-163): the composer's `/cycle now`. It publishes `cycle.start_requested` on
 * atlas.system (the record of the owner's request) and, with the same correlation id,
 * `orchestrator.cycle_now` on atlas.warroom, which is what the Orchestrator's hook consumes
 * (ATL-160: it starts a `manual` cycle, or posts "A cycle is already running" when one runs).
 * One audit row per request. Throttled to one per user per minute (cycles-start limiter).
 */
class CycleController extends Controller
{
    public const REQUESTED_EVENT = 'cycle.start_requested';

    public const CYCLE_NOW_EVENT = 'orchestrator.cycle_now';

    public function __construct(private readonly Auditable $audit, private readonly EventBus $bus) {}

    public function store(Request $request): JsonResponse
    {
        $env = ActiveEnv::get();
        $user = $request->user();
        $requestId = (string) Str::uuid();
        $correlation = 'cycle-request-'.$requestId;
        $payload = [
            'request_id' => $requestId,
            'kind' => 'manual',
            'requested_by' => $user !== null ? (string) $user->getAuthIdentifier() : null,
        ];

        $event = $this->bus->publish('atlas.system', self::REQUESTED_EVENT, $env, 'human:owner', $payload, $correlation);
        $this->bus->publish('atlas.warroom', self::CYCLE_NOW_EVENT, $env, 'human:owner', $payload, $correlation);

        $this->audit->record('cycle.start_requested', 'cycle_request', $requestId, null, ['env' => $env, 'event_id' => $event['id']]);

        return response()->json(['data' => ['request_id' => $requestId, 'env' => $env, 'event_id' => $event['id']]], 202);
    }
}
