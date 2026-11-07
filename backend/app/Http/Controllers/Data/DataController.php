<?php

declare(strict_types=1);

namespace App\Http\Controllers\Data;

use App\Http\Controllers\Controller;
use App\Http\Requests\Data\BackfillControlRequest;
use App\Http\Requests\Data\DataRequest;
use App\Support\Auditable;
use App\Support\Data\BackfillReport;
use App\Support\Data\StorageReport;
use App\Support\TradingCore;
use Carbon\CarbonImmutable;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\JsonResponse;
use RuntimeException;

/**
 * Settings → Data (brief §14.10, §16; ATL-48). Reads come from Postgres only; re-sync and pause
 * are relayed to trading_core's signed /internal/backfill/* endpoints (ATL-46) and audited.
 */
class DataController extends Controller
{
    public function __construct(private readonly Auditable $audit) {}

    public function backfill(DataRequest $request): JsonResponse
    {
        return response()->json(['data' => (new BackfillReport(CarbonImmutable::now()))->build($request->env())]);
    }

    public function storage(DataRequest $request): JsonResponse
    {
        return response()->json(['data' => (new StorageReport)->build()]);
    }

    public function resync(BackfillControlRequest $request): JsonResponse
    {
        return $this->relay($request, '/internal/backfill/start', 'data.resync_requested');
    }

    public function pause(BackfillControlRequest $request): JsonResponse
    {
        return $this->relay($request, '/internal/backfill/pause', 'data.pause_requested');
    }

    private function relay(BackfillControlRequest $request, string $path, string $action): JsonResponse
    {
        $payload = ['env' => $request->env(), 'inst_id' => $request->instId()];

        $status = 202;
        $error = null;
        $result = null;
        try {
            $response = TradingCore::fromConfig()->post($path, $payload);
            $body = $response->json();
            if (! $response->successful()) {
                [$status, $error] = [502, 'trading_core_error'];
            } else {
                $result = is_array($body) ? $body : [];
            }
        } catch (ConnectionException|RuntimeException) {
            [$status, $error] = [502, 'trading_core_unavailable'];
        }

        $this->audit->record(
            action: $action,
            subjectType: 'backfill_jobs',
            subjectId: null,
            before: null,
            after: [...$payload, 'ok' => $error === null, 'error' => $error],
        );

        if ($error !== null) {
            return response()->json(['ok' => false, 'error' => $error, 'message' => __("data.{$error}")], $status);
        }

        return response()->json(['ok' => true, ...$payload, 'trading_core' => $result], $status);
    }
}
