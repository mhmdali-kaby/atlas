<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\Watchlist;
use App\Models\WatchlistItem;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * One watchlist version with its items. `items` are the visible picks in rank order;
 * `removed_items` are the ones the owner removed (for Undo → restore), also in rank order.
 *
 * @mixin Watchlist
 */
class WatchlistResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var Watchlist $watchlist */
        $watchlist = $this->resource;

        return self::shape($watchlist);
    }

    /**
     * @return array<string, mixed>
     */
    public static function shape(Watchlist $watchlist): array
    {
        $items = $watchlist->items->sortBy('rank')->values();
        $visible = $items->filter(fn (WatchlistItem $i): bool => $i->removed_by_owner_at === null)->values();
        $removed = $items->filter(fn (WatchlistItem $i): bool => $i->removed_by_owner_at !== null)->values();

        return [
            'id' => $watchlist->id,
            'env' => $watchlist->env,
            'date' => $watchlist->date->toDateString(),
            'version' => $watchlist->version,
            'status' => $watchlist->status,
            'created_by' => $watchlist->created_by,
            'created_at' => $watchlist->created_at->utc()->toIso8601ZuluString('microsecond'),
            'edit_window_ends_at' => $watchlist->editWindowEndsAt()->utc()->toIso8601ZuluString('microsecond'),
            'edit_window_open' => $watchlist->editWindowOpen(),
            'reason' => $watchlist->reason,
            'message_id' => $watchlist->message_id,
            'data' => (object) $watchlist->data,
            'items' => $visible->map(fn (WatchlistItem $i): array => WatchlistItemResource::shape($i))->all(),
            'removed_items' => $removed->map(fn (WatchlistItem $i): array => WatchlistItemResource::shape($i))->all(),
        ];
    }
}
