<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\NewsSource;
use App\Support\DataSources\HourlyCounts;
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
    /** @var list<int> headlines per fetched hour, oldest first (ATL-71) */
    private array $hourly24h;

    private ?string $coveredBy = null;

    public function __construct(mixed $resource)
    {
        parent::__construct($resource);
        $this->hourly24h = array_fill(0, HourlyCounts::HOURS, 0);
    }

    /**
     * @param  list<int>  $hourly24h
     */
    public function withStats(array $hourly24h, ?string $coveredBy): static
    {
        $this->hourly24h = $hourly24h;
        $this->coveredBy = $coveredBy;

        return $this;
    }

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
            'provider' => $source->provider,
            'name' => $source->name,
            'kind' => $source->kind,
            'category' => $source->category,
            'url' => $source->url,
            'enabled' => $source->enabled,
            'requires_key' => $source->requires_key,
            // ATL-106: read-only trust flags behind a cluster's CONFIRMED/REPORTED/RUMOUR status.
            'reputable' => $source->reputable,
            'official' => $source->official,
            'has_key' => $hasKey,
            'key_last4' => $hasKey ? $source->apiKeyLast4() : null,
            'poll_seconds' => $source->poll_seconds,
            'status' => SourceStatus::of($source, CarbonImmutable::now()),
            'consecutive_failures' => $source->consecutive_failures,
            'disabled_until' => $source->disabled_until?->toIso8601ZuluString('microsecond'),
            'last_success_at' => $source->last_success_at?->toIso8601ZuluString('microsecond'),
            'last_item_at' => $source->last_item_at?->toIso8601ZuluString('microsecond'),
            'last_error' => $source->last_error,
            'hourly_24h' => $this->hourly24h,
            'covered_by' => $this->coveredBy,
            'updated_at' => $source->updated_at->toIso8601ZuluString('microsecond'),
        ];
    }
}
