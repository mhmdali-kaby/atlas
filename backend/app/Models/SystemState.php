<?php

declare(strict_types=1);

namespace App\Models;

use Carbon\CarbonInterface;
use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * The single system_state row (ATL-236, decision #255 (4)). Read the env through
 * App\Support\System\ActiveEnv, never this model directly.
 *
 * @property int $id always 1
 * @property 'demo'|'live' $active_env
 * @property CarbonInterface|null $live_started_at
 * @property CarbonInterface|null $switched_at
 * @property array<string, array{confirmed_at: string}> $preflight_attestations
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'system_state')]
#[Fillable(['id', 'active_env', 'live_started_at', 'switched_at', 'preflight_attestations'])]
class SystemState extends Model
{
    public const ID = 1;

    public $incrementing = false;

    protected $keyType = 'int';

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'id' => 'integer',
            'live_started_at' => 'datetime',
            'switched_at' => 'datetime',
            'preflight_attestations' => 'array',
        ];
    }
}
