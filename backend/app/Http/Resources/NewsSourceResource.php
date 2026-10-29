<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\NewsSource;
use App\Support\DataSources\SourceStatus;
use Carbon\CarbonImmutable;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * A news source as Settings → Data sources sees it (ATL-66). Never the key itself: only whether
 * one is stored and its last 4 characters (brief §1.6). last_error is written already redacted
 * by atlas_ingest.
 *
 * @mixin NewsSource
 */
class NewsSourceResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var NewsSource $source */
        $source = $this->resource;
        $hasKey = $source->getRawOriginal('api_key') !== null;

        return [
            'key' => $source->key,
            'name' => $source->name,
            'kind' => $source->kind,
            'category' => $source->category,
            'url' => $source->url,
            'enabled' => $source->enabled,
            'requires_key' => $source->requires_key,
            'has_key' => $hasKey,
            'key_last4' => $hasKey ? $source->apiKeyLast4() : null,
            'poll_seconds' => $source->poll_seconds,
            'status' => SourceStatus::of($source, CarbonImmutable::now()),
            'consecutive_failures' => $source->consecutive_failures,
            'disabled_until' => $source->disabled_until?->toIso8601ZuluString('microsecond'),
            'last_success_at' => $source->last_success_at?->toIso8601ZuluString('microsecond'),
            'last_item_at' => $source->last_item_at?->toIso8601ZuluString('microsecond'),
            'last_error' => $source->last_error,
            'updated_at' => $source->updated_at->toIso8601ZuluString('microsecond'),
        ];
    }
}
