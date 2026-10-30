<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\AlgoOrder;
use App\Support\Portfolio\Money;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * An algo order (stop-loss, take-profit, OCO, trigger) for GET /api/portfolio/orders?status=algo
 * (ATL-148). Decimal strings, never floats. `sz` null = the whole position; `ord_px` null = market.
 *
 * @mixin AlgoOrder
 */
class AlgoOrderResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var AlgoOrder $a */
        $a = $this->resource;

        return [
            'id' => $a->id,
            'env' => $a->env,
            'order_id' => $a->order_id,
            'inst_id' => $a->inst_id,
            'kind' => $a->kind,
            // An algo with a stop leg keeps the position protected (decision #251).
            'protects' => in_array($a->kind, ['sl', 'oco'], true),
            'side' => $a->side,
            'pos_side' => $a->pos_side,
            'trigger_px' => Money::str(Money::of($a->trigger_px)),
            'sl_trigger_px' => Money::str(Money::of($a->sl_trigger_px)),
            'tp_trigger_px' => Money::str(Money::of($a->tp_trigger_px)),
            'ord_px' => Money::str(Money::of($a->ord_px)),
            'sz' => Money::str(Money::of($a->sz)),
            'state' => $a->state,
            'origin' => $a->origin,
            'reduce_only' => $a->reduce_only,
            'algo_id' => $a->algo_id,
            'algo_cl_ord_id' => $a->algo_cl_ord_id,
            'last_error_code' => $a->last_error_code,
            'last_error_msg' => $a->last_error_msg,
            'created_at' => $a->created_at->utc()->format('Y-m-d\TH:i:s\Z'),
            'updated_at' => $a->updated_at->utc()->format('Y-m-d\TH:i:s\Z'),
        ];
    }
}
