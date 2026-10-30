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
 * One pick on a watchlist version (ATL-102). `removed_by_owner_at` hides it (the owner removed
 * it during the edit window); clearing it restores it at the same rank.
 *
 * @property string $id UUID
 * @property string $watchlist_id
 * @property string $inst_id
 * @property int $rank
 * @property string $asset_class
 * @property 'long'|'short'|'neutral' $bias
 * @property string $conviction decimal string, 0..1
 * @property string $thesis
 * @property list<array<string, mixed>> $catalysts
 * @property array<string, float|int> $key_levels
 * @property string $invalidation
 * @property bool $pinned_by_owner
 * @property Carbon|null $removed_by_owner_at
 * @property Carbon $data_as_of
 * @property Carbon $created_at
 * @property Carbon $updated_at
 * @property-read Watchlist $watchlist
 */
#[Table(name: 'watchlist_items')]
#[Fillable([
    'watchlist_id', 'inst_id', 'rank', 'asset_class', 'bias', 'conviction', 'thesis', 'catalysts',
    'key_levels', 'invalidation', 'pinned_by_owner', 'removed_by_owner_at', 'data_as_of',
])]
class WatchlistItem extends Model
{
    use HasUuids;

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    /**
     * @return BelongsTo<Watchlist, $this>
     */
    public function watchlist(): BelongsTo
    {
        return $this->belongsTo(Watchlist::class, 'watchlist_id');
    }

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'rank' => 'integer',
            'conviction' => 'decimal:4',
            'catalysts' => 'array',
            'key_levels' => 'array',
            'pinned_by_owner' => 'boolean',
            'removed_by_owner_at' => 'datetime',
            'data_as_of' => 'datetime',
        ];
    }
}
