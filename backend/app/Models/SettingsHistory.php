<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Support\Carbon;

/**
 * One saved version of a settings row. `diff` maps each changed field to {from, to}.
 *
 * @property string $id UUID
 * @property string $settings_id
 * @property int $version
 * @property array<string, int|string> $data
 * @property array<string, array{from: int|string|null, to: int|string|null}> $diff
 * @property string|null $changed_by
 * @property Carbon $created_at
 * @property-read User|null $changedBy
 */
#[Table(name: 'settings_history')]
#[Fillable(['settings_id', 'version', 'data', 'diff', 'changed_by'])]
class SettingsHistory extends Model
{
    use HasUuids;

    public const UPDATED_AT = null;

    /**
     * @return BelongsTo<User, $this>
     */
    public function changedBy(): BelongsTo
    {
        return $this->belongsTo(User::class, 'changed_by');
    }

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'version' => 'integer',
            'data' => 'array',
            'diff' => 'array',
        ];
    }
}
