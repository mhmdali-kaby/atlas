<?php

declare(strict_types=1);

namespace App\Http\Controllers\Risk;

use App\Http\Controllers\Controller;
use App\Http\Requests\Risk\RiskCheckRequest;
use App\Support\Auditable;
use App\Support\TradingCore;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\JsonResponse;
use RuntimeException;

/**
 * POST /api/risk/check (ATL-128, decision #249 F): the owner risk-checks a hand-written proposal.
 * The validated TradeProposal is relayed to trading_core's signed POST /internal/risk/check, which
 * stores it (author owner:manual) and runs the Risk Engine; nothing is sent to OKX. The decision is
 * returned as trading_core sent it, and every call is audited.
 */
class RiskCheckController extends Controller
{
    public function __construct(private readonly Auditable $audit) {}

    public function store(RiskCheckRequest $request): JsonResponse
    {
        $proposal = $request->proposal();

        $status = 200;
        $error = null;
        $body = [];
        try {
            $response = TradingCore::fromConfig()->post('/internal/risk/check', $proposal);
            $json = $response->json();
            $body = is_array($json) ? $json : [];
            if ($response->status() === 422) {
                [$status, $error] = [422, 'invalid_proposal'];
            } elseif ($response->status() === 409) {
                [$status, $error] = [409, 'risk_engine_unavailable'];
            } elseif (! $response->successful()) {
                [$status, $error] = [502, 'trading_core_error'];
            }
        } catch (ConnectionException|RuntimeException) {
            [$status, $error] = [502, 'trading_core_unavailable'];
        }

        $proposalId = $error === null && is_string($body['proposal_id'] ?? null) ? $body['proposal_id'] : null;
        $this->audit->record(
            action: 'risk.manual_check',
            subjectType: 'proposals',
            subjectId: $proposalId,
            before: null,
            after: [
                'inst_id' => $proposal['inst_id'] ?? null,
                'side' => $proposal['side'] ?? null,
                'ok' => $error === null,
                'error' => $error,
                'risk_check_id' => $error === null ? ($body['risk_check_id'] ?? null) : null,
                'result' => $error === null ? ($body['result'] ?? null) : null,
                'decision' => $error === null ? ($body['decision'] ?? null) : null,
            ],
        );

        if ($error !== null) {
            $out = ['ok' => false, 'error' => $error, 'message' => __("risk.{$error}")];
            if ($error === 'invalid_proposal') {
                $out['errors'] = $body['errors'] ?? [];
            } elseif ($error === 'risk_engine_unavailable') {
                $out['detail'] = $body['detail'] ?? null;
            }

            return response()->json($out, $status);
        }

        return response()->json($body, 200);
    }
}
