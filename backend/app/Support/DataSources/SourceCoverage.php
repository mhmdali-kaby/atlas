<?php

declare(strict_types=1);

namespace App\Support\DataSources;

use App\Models\NewsSource;
use Illuminate\Support\Collection;

/**
 * Which source takes over another's job (ATL-71, decision #244: Finnhub's economic calendar
 * replaces ForexFactory once keyed). The mapping lives in config/defaults.yaml as
 * `news.sources.<key>.covers: [<covered key>, …]`; a covering source counts only while it is
 * enabled and has a key stored.
 */
final class SourceCoverage
{
    /**
     * @param  Collection<int, NewsSource>  $sources  every source (a coverer may be outside a filtered list)
     * @return array<string, string|null> source key => key of the source covering it, or null
     */
    public static function coveredBy(Collection $sources): array
    {
        $byKey = $sources->keyBy('key');
        $result = $sources->mapWithKeys(fn (NewsSource $s): array => [$s->key => null])->all();

        foreach (self::mapping() as $coverer => $covered) {
            $source = $byKey->get($coverer);
            if (! $source instanceof NewsSource || ! self::canCover($source)) {
                continue;
            }
            foreach ($covered as $key) {
                if (array_key_exists($key, $result) && $result[$key] === null) {
                    $result[$key] = $coverer;
                }
            }
        }

        return $result;
    }

    private static function canCover(NewsSource $source): bool
    {
        return $source->enabled && $source->getRawOriginal('api_key') !== null;
    }

    /**
     * @return array<string, list<string>> coverer key => keys it covers
     */
    private static function mapping(): array
    {
        /** @var array<string, array{covers?: list<string>}> $sources */
        $sources = (array) config('atlas_defaults.news.sources');
        $mapping = [];
        foreach ($sources as $key => $source) {
            $covers = $source['covers'] ?? [];
            if ($covers !== []) {
                $mapping[$key] = $covers;
            }
        }

        return $mapping;
    }
}
