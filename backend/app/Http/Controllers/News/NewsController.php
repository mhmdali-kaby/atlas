<?php

declare(strict_types=1);

namespace App\Http\Controllers\News;

use App\Casts\PostgresTextArray;
use App\Http\Controllers\Controller;
use App\Http\Requests\News\CalendarRequest;
use App\Http\Requests\News\LatestSentimentRequest;
use App\Http\Requests\News\SearchNewsRequest;
use App\Http\Resources\NewsItemResource;
use App\Support\News\NewsFormat;
use App\Support\News\NewsSearch;
use Carbon\CarbonImmutable;
use Illuminate\Database\Query\Builder;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Resources\Json\AnonymousResourceCollection;
use Illuminate\Support\Facades\DB;

/**
 * Read-only news, cluster, calendar and sentiment API (ATL-67, brief §16). Reads Postgres only;
 * ingest (services/ingest) writes these tables.
 */
class NewsController extends Controller
{
    /** Columns a list row returns (embedding, body and raw stay out of the API). */
    private const ITEM_COLUMNS = [
        'id', 'title', 'title_original', 'language', 'source', 'url', 'published_at',
        'instruments', 'asset_classes', 'event_type', 'sentiment', 'importance', 'novelty',
        'cluster_id',
    ];

    public function index(SearchNewsRequest $request): AnonymousResourceCollection
    {
        $filtered = $this->filteredItems($request);

        if ($request->clustered()) {
            // One row per story: the newest matching item of each cluster (an item with no
            // cluster is its own story), with the count and earliest time of the matching items.
            $group = 'coalesce(cluster_id, id)';
            // Window functions run before DISTINCT ON, which keeps the first row per group below.
            $collapsed = $filtered
                ->selectRaw(
                    "distinct on ({$group}) ".implode(', ', self::ITEM_COLUMNS)
                    .", count(*) over (partition by {$group}) as item_count"
                    .", min(published_at) over (partition by {$group}) as first_seen",
                )
                ->orderByRaw($group)
                ->orderByDesc('published_at')
                ->orderByDesc('id');

            $query = DB::query()->fromSub($collapsed, 'news_items');
        } else {
            $query = $filtered->select(self::ITEM_COLUMNS);
        }

        $page = $query
            ->orderByDesc('published_at')
            ->orderByDesc('id')
            ->cursorPaginate($request->limit())
            ->withQueryString();

        return NewsItemResource::collection($page);
    }

    public function cluster(string $cluster): JsonResponse
    {
        $row = DB::table('news_clusters')->where('id', $cluster)->first();
        abort_if($row === null, 404);
        $data = (array) $row;

        $items = DB::table('news_items')
            ->select(self::ITEM_COLUMNS)
            ->where('cluster_id', $cluster)
            ->orderByDesc('published_at')
            ->orderByDesc('id')
            ->get();

        return response()->json(['data' => [
            'id' => $data['id'],
            'headline' => $data['headline'],
            'first_seen' => NewsFormat::timestamp($data['first_seen']),
            'last_seen' => NewsFormat::timestamp($data['last_seen']),
            'item_count' => (int) $data['item_count'],
            'max_importance' => (int) $data['max_importance'],
            'instruments' => PostgresTextArray::parse(is_string($data['instruments']) ? $data['instruments'] : '{}'),
            'items' => NewsItemResource::collection($items)->resolve(),
        ]]);
    }

    public function calendar(CalendarRequest $request): JsonResponse
    {
        $from = $request->from();
        $to = $request->to();
        $limit = $request->limit();
        $events = [];

        if ($request->kind() !== 'earnings') {
            $economic = DB::table('economic_events')
                ->where('scheduled_at', '>=', self::ts($from))
                ->where('scheduled_at', '<=', self::ts($to))
                ->when($request->minImportance(), fn (Builder $q, int $min) => $q->where('importance', '>=', $min))
                ->orderBy('scheduled_at')
                ->orderBy('id')
                ->limit($limit)
                ->get();
            foreach ($economic as $row) {
                $events[] = self::economicEvent((array) $row);
            }
        }

        if ($request->kind() !== 'economic') {
            foreach ($this->earningsBetween($from, $to, $limit) as $row) {
                $events[] = self::earningsEvent((array) $row);
            }
        }

        usort($events, static fn (array $a, array $b): int => [$a['at'], $a['kind'], $a['id']] <=> [$b['at'], $b['kind'], $b['id']]);

        return response()->json([
            'data' => array_slice($events, 0, $limit),
            'meta' => [
                'from' => $from->toIso8601ZuluString(),
                'to' => $to->toIso8601ZuluString(),
                'kind' => $request->kind(),
                'limit' => $limit,
            ],
        ]);
    }

    public function sentiment(LatestSentimentRequest $request): JsonResponse
    {
        $row = DB::table('market_sentiment')
            ->where('index_key', $request->indexKey())
            ->orderByDesc('ts')
            ->first();

        if ($row === null) {
            return response()->json(['data' => null]);
        }
        $data = (array) $row;

        return response()->json(['data' => [
            'index_key' => $data['index_key'],
            'value' => NewsFormat::decimal($data['value']),
            'classification' => $data['classification'],
            'ts' => NewsFormat::timestamp($data['ts']),
        ]]);
    }

    private function filteredItems(SearchNewsRequest $request): Builder
    {
        $query = DB::table('news_items');

        if (($instIds = $request->instIds()) !== []) {
            // `&&` is in GIN array_ops, so news_items_instruments_gin serves this filter.
            $query->whereRaw('instruments && ?::text[]', [PostgresTextArray::serialize($instIds)]);
        }
        if (($assetClass = $request->assetClass()) !== null) {
            $query->whereRaw('asset_classes @> ?::text[]', [PostgresTextArray::serialize([$assetClass])]);
        }
        if (($eventType = $request->eventType()) !== null) {
            $query->where('event_type', $eventType);
        }
        if (($min = $request->minImportance()) !== null) {
            $query->where('importance', '>=', $min);
        }
        if (($since = $request->since()) !== null) {
            $query->where('published_at', '>=', self::ts($since));
        }
        if (($until = $request->until()) !== null) {
            $query->where('published_at', '<=', self::ts($until));
        }
        if (($q = $request->search()) !== null) {
            $query->whereRaw(NewsSearch::TS_DOCUMENT.' @@ '.NewsSearch::TS_QUERY, [$q]);
        }

        return $query;
    }

    /**
     * Earnings rows have a date and Finnhub's hour hint; place bmo/amc at the configured
     * US/Eastern times (Postgres applies DST), anything else at 00:00 UTC of the date.
     *
     * @return iterable<object>
     */
    private function earningsBetween(CarbonImmutable $from, CarbonImmutable $to, int $limit): iterable
    {
        $zone = (string) config('atlas_defaults.news.api.earnings_timezone', 'America/New_York');
        $bmo = (string) config('atlas_defaults.news.api.earnings_bmo_time', '09:30');
        $amc = (string) config('atlas_defaults.news.api.earnings_amc_time', '16:00');

        $placed = DB::table('earnings_events')
            ->select('*')
            ->selectRaw(
                "case lower(coalesce(time, ''))
                    when 'bmo' then (date + ?::time) at time zone ?
                    when 'amc' then (date + ?::time) at time zone ?
                    else date::timestamp at time zone 'UTC'
                 end as at",
                [$bmo, $zone, $amc, $zone],
            )
            // A day of margin either side: the placed time can cross a UTC date boundary.
            ->where('date', '>=', $from->subDay()->toDateString())
            ->where('date', '<=', $to->addDay()->toDateString());

        return DB::query()->fromSub($placed, 'earnings')
            ->where('at', '>=', self::ts($from))
            ->where('at', '<=', self::ts($to))
            ->orderBy('at')
            ->orderBy('id')
            ->limit($limit)
            ->get();
    }

    /**
     * @param  array<string, mixed>  $row
     * @return array<string, mixed>
     */
    private static function economicEvent(array $row): array
    {
        return [
            'kind' => 'economic',
            'id' => $row['id'],
            'at' => NewsFormat::timestamp($row['scheduled_at']),
            'name' => $row['name'],
            'country' => $row['country'],
            'importance' => (int) $row['importance'],
            'actual' => $row['actual'],
            'forecast' => $row['forecast'],
            'previous' => $row['previous'],
            'surprise' => NewsFormat::decimal($row['surprise']),
            'source' => $row['source'],
        ];
    }

    /**
     * @param  array<string, mixed>  $row
     * @return array<string, mixed>
     */
    private static function earningsEvent(array $row): array
    {
        $hint = is_string($row['time']) ? strtolower($row['time']) : null;

        return [
            'kind' => 'earnings',
            'id' => $row['id'],
            'at' => NewsFormat::timestamp($row['at']),
            'symbol' => $row['symbol'],
            'date' => $row['date'],
            'time_hint' => $row['time'],
            'time_known' => in_array($hint, ['bmo', 'amc'], true),
            'eps_est' => NewsFormat::decimal($row['eps_est']),
            'eps_act' => NewsFormat::decimal($row['eps_act']),
            'rev_est' => NewsFormat::decimal($row['rev_est']),
            'rev_act' => NewsFormat::decimal($row['rev_act']),
        ];
    }

    private static function ts(CarbonImmutable $at): string
    {
        return $at->utc()->format('Y-m-d\TH:i:s.uP');
    }
}
