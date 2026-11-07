<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Collection;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Support\Carbon;

/**
 * One saved version of an env's watchlist for an Asia/Baghdad business day (ATL-102). The
 * current watchlist is the highest version for (env, date). Rows are written by Market Scout
 * (atlas_agents.watchlists.save_watchlist); Laravel only edits its items (ATL-103).
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property Carbon $date
 * @property int $version
 * @property string $created_by
 * @property 'draft'|'published'|'partial_evidence'|'empty' $status
 * @property Carbon|null $edit_window_ends_at
 * @property string|null $reason
 * @property string|null $message_id
 * @property array<string, mixed> $data
 * @property Carbon $created_at
 * @property Carbon $updated_at
 * @property-read Collection<int, WatchlistItem> $items
 */
#[Table(name: 'watchlists')]
#[Fillable(['env', 'date', 'version', 'created_by', 'status', 'edit_window_ends_at', 'reason', 'message_id', 'data'])]
class Watchlist extends Model
{
    use HasUuids;

    public const STATUSES = ['draft', 'published', 'partial_evidence', 'empty'];

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    /**
     * @return HasMany<WatchlistItem, $this>
     */
    public function items(): HasMany
    {
        return $this->hasMany(WatchlistItem::class, 'watchlist_id');
    }

    /**
     * The end of the owner's pin/remove window: the stored value, else created_at +
     * agents.scout.watchlist_edit_window_minutes.
     */
    public function editWindowEndsAt(): Carbon
    {
        if ($this->edit_window_ends_at !== null) {
            return $this->edit_window_ends_at;
        }
        $minutes = config('atlas_defaults.agents.scout.watchlist_edit_window_minutes');

        return $this->created_at->copy()->addMinutes(is_int($minutes) ? $minutes : 10);
    }

    public function editWindowOpen(?Carbon $now = null): bool
    {
        return ($now ?? Carbon::now())->lt($this->editWindowEndsAt());
    }

    /**
     * Whether this row is the highest version for its (env, date).
     */
    public function isCurrentVersion(): bool
    {
        $max = self::query()
            ->where('env', $this->env)
            ->whereDate('date', $this->date->toDateString())
            ->max('version');

        return (int) $max === $this->version;
    }

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'date' => 'date:Y-m-d',
            'version' => 'integer',
            'edit_window_ends_at' => 'datetime',
            'data' => 'array',
        ];
    }
}
