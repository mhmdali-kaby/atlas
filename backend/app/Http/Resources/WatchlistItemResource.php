<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\WatchlistItem;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * @mixin WatchlistItem
 */
class WatchlistItemResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var WatchlistItem $item */
        $item = $this->resource;

        return self::shape($item);
    }

    /**
     * Also used for the watchlist.updated event payload.
     *
     * @return array<string, mixed>
     */
    public static function shape(WatchlistItem $item): array
    {
        return [
            'id' => $item->id,
            'inst_id' => $item->inst_id,
            'rank' => $item->rank,
            'asset_class' => $item->asset_class,
            'bias' => $item->bias,
            'conviction' => $item->conviction,
            'thesis' => $item->thesis,
            'catalysts' => $item->catalysts,
            'key_levels' => (object) $item->key_levels,
            'invalidation' => $item->invalidation,
            'pinned_by_owner' => $item->pinned_by_owner,
            'removed_by_owner_at' => $item->removed_by_owner_at?->utc()->toIso8601ZuluString('microsecond'),
            'data_as_of' => $item->data_as_of->utc()->toIso8601ZuluString('microsecond'),
        ];
    }
}
