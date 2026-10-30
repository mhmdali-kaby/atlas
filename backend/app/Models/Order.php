<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * An OKX order row (ATL-140/143). Written only by trading_core's Executor and sync; read-only here.
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property string|null $proposal_id
 * @property string|null $risk_check_id
 * @property string|null $cl_ord_id
 * @property string|null $ord_id
 * @property string $inst_id
 * @property 'buy'|'sell' $side
 * @property string $ord_type
 * @property string|null $px
 * @property string $sz
 * @property string $filled_sz
 * @property string|null $avg_px
 * @property int|null $lever
 * @property string $state
 * @property string $purpose
 * @property bool $reduce_only
 * @property string|null $last_error_code
 * @property string|null $last_error_msg
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'orders')]
class Order extends Model
{
    use HasUuids;

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    protected $guarded = ['*'];

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'lever' => 'integer',
            'reduce_only' => 'boolean',
        ];
    }
}
