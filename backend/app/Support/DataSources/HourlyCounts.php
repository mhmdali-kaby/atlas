<?php

declare(strict_types=1);

namespace App\Support\DataSources;

use Carbon\CarbonInterface;
use Illuminate\Support\Facades\DB;

/**
 * Headlines per source per hour for the last 24 hours (ATL-71, Settings → Data sources
 * sparkline). news_items are bucketed by fetched_at (UTC hour), oldest first, the last slot being
 * the current hour. One grouped query for every source.
 */
final class HourlyCounts
{
    public const HOURS = 24;

    /**
     * @param  list<string>  $sourceKeys
     * @return array<string, list<int>> source key => exactly 24 counts, zeros for empty hours
     */
    public static function forSources(array $sourceKeys, CarbonInterface $now): array
    {
        if ($sourceKeys === []) {
            return [];
        }

        $windowStart = $now->copy()->utc()->startOfHour()->subHours(self::HOURS - 1);
        $windowEnd = $windowStart->copy()->addHours(self::HOURS);

        /** @var list<object{source: string, slot: int|string, n: int|string}> $rows */
        $rows = DB::table('news_items')
            ->selectRaw('source, floor(extract(epoch from (fetched_at - ?::timestamptz)) / 3600)::int AS slot, count(*) AS n', [
                $windowStart->toIso8601ZuluString('microsecond'),
            ])
            ->whereIn('source', $sourceKeys)
            ->where('fetched_at', '>=', $windowStart)
            ->where('fetched_at', '<', $windowEnd)
            ->groupBy('source', 'slot')
            ->get()
            ->all();

        /** @var array<string, array<int, int>> $counts */
        $counts = [];
        foreach ($rows as $row) {
            $counts[$row->source][(int) $row->slot] = (int) $row->n;
        }

        $result = [];
        foreach ($sourceKeys as $key) {
            $series = [];
            for ($slot = 0; $slot < self::HOURS; $slot++) {
                $series[] = $counts[$key][$slot] ?? 0;
            }
            $result[$key] = $series;
        }

        return $result;
    }
}
