<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Support\Carbon;

/**
 * The versioned settings row for one env (brief §14). `data` is authoritative; the typed
 * columns mirror the hot fields. Write it only through App\Support\Settings\SettingsService.
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property int $version
 * @property array<string, int|string> $data
 * @property string $risk_profile
 * @property int $autonomy_level
 * @property string $max_loss_per_trade_pct
 * @property string $max_position_pct
 * @property int $max_leverage
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'settings')]
#[Fillable(['env', 'version', 'data', 'risk_profile', 'autonomy_level', 'max_loss_per_trade_pct', 'max_position_pct', 'max_leverage'])]
class Setting extends Model
{
    use HasUuids;

    /**
     * @return HasMany<SettingsHistory, $this>
     */
    public function history(): HasMany
    {
        return $this->hasMany(SettingsHistory::class, 'settings_id');
    }

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'version' => 'integer',
            'data' => 'array',
            'autonomy_level' => 'integer',
            'max_leverage' => 'integer',
        ];
    }
}
