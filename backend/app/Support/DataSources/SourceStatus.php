<?php

declare(strict_types=1);

namespace App\Support\DataSources;

use App\Models\NewsSource;
use Carbon\CarbonInterface;
use Illuminate\Support\Collection;

/**
 * Derives a news source's health from its row (ATL-66). Status is not stored: the owner's toggle
 * and key decide disabled/needs_key, atlas_ingest's failure columns decide degraded.
 *
 * Precedence: disabled > needs_key > degraded > never_run > ok.
 */
final class SourceStatus
{
    public const OK = 'ok';

    public const DEGRADED = 'degraded';

    public const NEEDS_KEY = 'needs_key';

    public const DISABLED = 'disabled';

    public const NEVER_RUN = 'never_run';

    /** Statuses in which a source delivers nothing. */
    public const DOWN = [self::DISABLED, self::NEEDS_KEY, self::DEGRADED];

    public static function of(NewsSource $source, CarbonInterface $now): string
    {
        if (! $source->enabled) {
            return self::DISABLED;
        }
        if ($source->requires_key && $source->getRawOriginal('api_key') === null) {
            return self::NEEDS_KEY;
        }
        if ($source->disabled_until !== null && $source->disabled_until->greaterThan($now)) {
            return self::DEGRADED;
        }
        if ($source->last_success_at === null && $source->last_error === null && $source->consecutive_failures === 0) {
            return self::NEVER_RUN;
        }

        return self::OK;
    }

    /**
     * One entry per category: how many sources it has, how many can deliver, and whether all are
     * down (patch E: News Intelligence says so and Market Scout caps conviction for that class).
     *
     * @param  Collection<int, NewsSource>  $sources
     * @return array<string, array{total: int, available: int, all_down: bool}>
     */
    public static function summary(Collection $sources, CarbonInterface $now): array
    {
        $summary = [];
        foreach (NewsSource::CATEGORIES as $category) {
            $inCategory = $sources->where('category', $category);
            $available = $inCategory
                ->filter(fn (NewsSource $s): bool => ! in_array(self::of($s, $now), self::DOWN, true))
                ->count();
            $summary[$category] = [
                'total' => $inCategory->count(),
                'available' => $available,
                'all_down' => $available === 0,
            ];
        }

        return $summary;
    }
}
