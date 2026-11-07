<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * An OKX algo order (stop-loss, take-profit, OCO, trigger; ATL-141). Written only by trading_core's
 * Executor, lifecycle and reconciliation; read-only here.
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property string|null $order_id
 * @property string|null $algo_id
 * @property string|null $algo_cl_ord_id
 * @property string $inst_id
 * @property 'sl'|'tp'|'oco'|'trigger' $kind
 * @property 'buy'|'sell'|null $side
 * @property 'net'|'long'|'short'|null $pos_side
 * @property string|null $trigger_px
 * @property string|null $sl_trigger_px
 * @property string|null $tp_trigger_px
 * @property string|null $ord_px
 * @property string|null $sz null = the whole position
 * @property string $state
 * @property bool $reduce_only
 * @property 'atlas'|'external' $origin
 * @property string|null $last_error_code
 * @property string|null $last_error_msg
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'algo_orders')]
class AlgoOrder extends Model
{
    use HasUuids;

    /** States in which an algo order is still pending on OKX (ours before sending, or OKX's live/pause). */
    public const OPEN_STATES = ['new', 'sending', 'live', 'pause', 'partially_effective'];

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    protected $guarded = ['*'];

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'reduce_only' => 'boolean',
        ];
    }
}
