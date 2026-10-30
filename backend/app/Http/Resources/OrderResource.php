<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\Order;
use App\Support\Portfolio\Money;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * An order for GET /api/portfolio/orders (ATL-148). Prices and sizes are decimal strings with no
 * trailing zeros (never floats); sizes are in contracts (OKX `sz`).
 *
 * @mixin Order
 */
class OrderResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var Order $o */
        $o = $this->resource;

        return [
            'id' => $o->id,
            'env' => $o->env,
            'inst_id' => $o->inst_id,
            'side' => $o->side,
            'pos_side' => $o->pos_side,
            'ord_type' => $o->ord_type,
            'td_mode' => $o->td_mode,
            'px' => Money::str(Money::of($o->px)),
            'sz' => Money::str(Money::of($o->sz)) ?? '0',
            'filled_sz' => Money::str(Money::of($o->filled_sz)) ?? '0',
            'avg_px' => Money::str(Money::of($o->avg_px)),
            'lever' => $o->lever,
            'state' => $o->state,
            'purpose' => $o->purpose,
            'origin' => $o->origin,
            'reduce_only' => $o->reduce_only,
            'cl_ord_id' => $o->cl_ord_id,
            'ord_id' => $o->ord_id,
            'proposal_id' => $o->proposal_id,
            'last_error_code' => $o->last_error_code,
            'last_error_msg' => $o->last_error_msg,
            'can_cancel' => in_array($o->state, Order::OPEN_STATES, true),
            'created_at' => $o->created_at->utc()->format('Y-m-d\TH:i:s\Z'),
            'updated_at' => $o->updated_at->utc()->format('Y-m-d\TH:i:s\Z'),
        ];
    }
}
