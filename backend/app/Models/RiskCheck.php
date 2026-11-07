<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * One Risk Engine decision on a proposal (brief §10). Written only by trading_core; read-only here.
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property string $proposal_id
 * @property int $settings_version
 * @property 'approved'|'resized'|'rejected' $result
 * @property list<array<string, mixed>> $checks
 * @property string|null $final_size
 * @property string|null $final_notional_usd
 * @property int|null $leverage
 * @property string|null $reason
 * @property 'send_now'|'awaiting_owner'|'advise_only'|null $decision
 * @property Carbon $created_at
 */
#[Table(name: 'risk_checks')]
class RiskCheck extends Model
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
            'settings_version' => 'integer',
            'checks' => 'array',
            'leverage' => 'integer',
        ];
    }
}
