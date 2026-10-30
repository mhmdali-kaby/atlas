<?php

declare(strict_types=1);

namespace App\Http\Controllers\System;

use App\Http\Controllers\Controller;
use App\Http\Requests\System\ClosePositionRequest;
use App\Models\Order;
use App\Support\Auditable;
use App\Support\System\ActiveEnv;
use App\Support\System\ControlCall;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Gate;

/**
 * The owner's one-order cancel and one-position close (brief §13, §16; ATL-148), forwarded to
 * trading_core's signed routes (ATL-147). Both are scoped to the active env: an id from the other
 * env answers 404 and nothing is sent. Every forwarded call writes one audit_log row.
 */
class TradeActionController extends Controller
{
    public function __construct(private readonly Auditable $audit) {}

    /** POST /api/orders/{order}/cancel — {order} is our orders.id. */
    public function cancelOrder(string $order): JsonResponse
    {
        $env = ActiveEnv::get();
        $model = Order::query()->where('env', $env)->whereKey($order)->first();
        if ($model === null) {
            return $this->notFound('order_not_found');
        }
        Gate::authorize('cancel', $model);

        $call = ControlCall::post("/internal/orders/{$model->id}/cancel");
        $this->audit->record('order.cancel_requested', 'orders', $model->id, [
            'env' => $env,
            'inst_id' => $model->inst_id,
            'state' => $model->state,
            'purpose' => $model->purpose,
        ], $call->auditOutcome());

        return response()->json($call->body, $call->status);
    }

    /**
     * POST /api/portfolio/positions/{position}/close {"confirm": true} — {position} is the `id` the
     * positions list returns (a positions_snapshots row). trading_core closes that instrument and
     * side at market (reduce-only) and cancels its algos.
     */
    public function closePosition(ClosePositionRequest $request, string $position): JsonResponse
    {
        $env = ActiveEnv::get();
        $row = DB::table('positions_snapshots')->where('env', $env)->where('id', $position)->first(['id', 'inst_id', 'pos_side', 'pos']);
        if ($row === null) {
            return $this->notFound('position_not_found');
        }
        $instId = (string) $row->inst_id;
        $posSide = is_string($row->pos_side) && in_array($row->pos_side, ['net', 'long', 'short'], true) ? $row->pos_side : null;

        $call = ControlCall::post('/internal/positions/'.rawurlencode($instId).'/close', $posSide === null ? [] : ['pos_side' => $posSide]);
        $this->audit->record('position.close_requested', 'positions', (string) $row->id, [
            'env' => $env,
            'inst_id' => $instId,
            'pos_side' => $posSide,
            'pos' => is_scalar($row->pos) ? (string) $row->pos : null,
        ], $call->auditOutcome());

        return response()->json($call->body, $call->status);
    }

    private function notFound(string $error): JsonResponse
    {
        return response()->json(['ok' => false, 'error' => $error, 'message' => __("system.{$error}")], 404);
    }
}
