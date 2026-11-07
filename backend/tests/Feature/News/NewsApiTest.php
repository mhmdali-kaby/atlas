<?php

declare(strict_types=1);

use App\Casts\PostgresTextArray;
use App\Models\User;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

/**
 * @param  array<string, mixed>  $overrides
 */
function atl67Item(array $overrides = []): string
{
    $id = (string) Str::uuid();
    $instruments = $overrides['instruments'] ?? [];
    $assetClasses = $overrides['asset_classes'] ?? [];
    unset($overrides['instruments'], $overrides['asset_classes']);
    $url = 'https://example.test/'.$id;

    DB::table('news_items')->insert(array_merge([
        'id' => $id,
        'source' => 'rss.coindesk',
        'url' => $url,
        'canonical_url' => $url,
        'title' => 'Headline '.$id,
        'summary' => null,
        'published_at' => '2026-10-29T06:00:00Z',
        'fetched_at' => '2026-10-29T06:00:05Z',
        'language' => 'en',
        'instruments' => PostgresTextArray::serialize($instruments),
        'asset_classes' => PostgresTextArray::serialize($assetClasses),
        'importance' => 2,
        'created_at' => now(),
        'updated_at' => now(),
    ], $overrides));

    return $id;
}

function atl67Cluster(string $headline = 'Story'): string
{
    $id = (string) Str::uuid();
    DB::table('news_clusters')->insert([
        'id' => $id, 'headline' => $headline, 'first_seen' => '2026-10-29T00:00:00Z',
        'last_seen' => '2026-10-29T00:00:00Z', 'item_count' => 0, 'max_importance' => 1,
        'instruments' => '{}', 'created_at' => now(), 'updated_at' => now(),
    ]);

    return $id;
}

beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
});

afterEach(function (): void {
    CarbonImmutable::setTestNow();
});

it('filters inst_ids[] to items whose instruments contain it, newest first', function (): void {
    $older = atl67Item(['instruments' => ['BTC-USDT', 'BTC-USDT-SWAP'], 'published_at' => '2026-10-29T01:00:00Z']);
    $newer = atl67Item(['instruments' => ['BTC-USDT'], 'published_at' => '2026-10-29T03:00:00Z']);
    $middle = atl67Item(['instruments' => ['ETH-USDT', 'BTC-USDT'], 'published_at' => '2026-10-29T02:00:00Z']);
    atl67Item(['instruments' => ['BTC-USDT-SWAP'], 'published_at' => '2026-10-29T04:00:00Z']);
    atl67Item(['instruments' => ['ETH-USDT'], 'published_at' => '2026-10-29T05:00:00Z']);
    atl67Item(['instruments' => [], 'published_at' => '2026-10-29T05:30:00Z']);

    $response = $this->getJson('/api/news?inst_ids[]=BTC-USDT')->assertOk();

    expect(array_column($response->json('data'), 'id'))->toBe([$newer, $middle, $older]);
    foreach ($response->json('data') as $row) {
        expect($row['instruments'])->toContain('BTC-USDT');
    }
    expect($response->json('data.0'))->toHaveKeys([
        'id', 'title', 'title_original', 'language', 'source', 'url', 'published_at', 'instruments',
        'asset_classes', 'event_type', 'sentiment', 'importance', 'novelty', 'cluster_id',
    ])->and($response->json('data.0.published_at'))->toBe('2026-10-29T03:00:00Z');
});

it('serves the inst_ids filter from the GIN index on instruments', function (): void {
    // Enough rows, with a selective match, that the planner has a real choice to make.
    DB::statement(<<<'SQL'
        insert into news_items (id, source, url, canonical_url, title, published_at, fetched_at,
                                instruments, importance, created_at, updated_at)
        select gen_random_uuid(), 'rss.coindesk', 'https://example.test/g/' || g,
               'https://example.test/g/' || g, 'Bulk ' || g,
               timestamptz '2026-10-01T00:00:00Z' + g * interval '1 minute',
               timestamptz '2026-10-01T00:00:00Z' + g * interval '1 minute',
               case when g % 200 = 0 then array['BTC-USDT', 'BTC-USDT-SWAP']
                    else array['COIN' || (g % 150) || '-USDT'] end,
               1, now(), now()
        from generate_series(1, 20000) as g
        SQL);
    DB::statement('analyze news_items');

    $captured = null;
    DB::listen(function ($query) use (&$captured): void {
        if (str_contains($query->sql, 'instruments &&')) {
            $captured = $query;
        }
    });

    $response = $this->getJson('/api/news?inst_ids[]=BTC-USDT&limit=20')->assertOk();
    expect($response->json('data'))->toHaveCount(20)
        ->and($captured)->not->toBeNull();

    $plan = implode("\n", array_map(
        fn (object $row): string => (string) ((array) $row)['QUERY PLAN'],
        DB::select('explain '.$captured->sql, $captured->bindings),
    ));

    expect($plan)->toContain('news_items_instruments_gin')
        ->and($plan)->toContain('instruments &&');
});

it('collapses cluster=1 to one row per cluster with item_count and earliest first_seen', function (): void {
    $storyA = atl67Cluster('A');
    $storyB = atl67Cluster('B');
    atl67Item(['cluster_id' => $storyA, 'published_at' => '2026-10-29T01:00:00Z']);
    atl67Item(['cluster_id' => $storyA, 'published_at' => '2026-10-29T00:15:00Z']);
    $headA = atl67Item(['cluster_id' => $storyA, 'published_at' => '2026-10-29T04:00:00Z']);
    $headB = atl67Item(['cluster_id' => $storyB, 'published_at' => '2026-10-29T02:00:00Z']);
    atl67Item(['cluster_id' => $storyB, 'published_at' => '2026-10-29T01:30:00Z']);
    $loose = atl67Item(['cluster_id' => null, 'published_at' => '2026-10-29T03:00:00Z']);

    $rows = $this->getJson('/api/news?cluster=1')->assertOk()->json('data');

    expect($rows)->toHaveCount(3)
        ->and(array_column($rows, 'id'))->toBe([$headA, $loose, $headB])
        ->and($rows[0])->toMatchArray(['cluster_id' => $storyA, 'item_count' => 3, 'first_seen' => '2026-10-29T00:15:00Z'])
        ->and($rows[1])->toMatchArray(['cluster_id' => null, 'item_count' => 1, 'first_seen' => '2026-10-29T03:00:00Z'])
        ->and($rows[2])->toMatchArray(['cluster_id' => $storyB, 'item_count' => 2, 'first_seen' => '2026-10-29T01:30:00Z']);

    // Without cluster=1 every item is listed.
    expect($this->getJson('/api/news')->assertOk()->json('data'))->toHaveCount(6);
});

it('pages clustered results by cursor without repeating a story', function (): void {
    $seen = [];
    for ($i = 0; $i < 7; $i++) {
        $cluster = atl67Cluster("S{$i}");
        atl67Item(['cluster_id' => $cluster, 'published_at' => "2026-10-29T0{$i}:00:00Z"]);
        atl67Item(['cluster_id' => $cluster, 'published_at' => "2026-10-29T0{$i}:30:00Z"]);
    }

    $cursor = null;
    do {
        $response = $this->getJson('/api/news?'.http_build_query(array_filter(['cluster' => 1, 'limit' => 3, 'cursor' => $cursor])))->assertOk();
        foreach ($response->json('data') as $row) {
            $seen[] = $row['cluster_id'];
            expect($row['item_count'])->toBe(2);
        }
        $cursor = $response->json('meta.next_cursor');
    } while ($cursor !== null);

    expect($seen)->toHaveCount(7)->and(array_unique($seen))->toHaveCount(7);
});

it('applies q, asset_class, event_type, min_importance and since/until', function (): void {
    $hit = atl67Item([
        'title' => 'Gold rallies as Fed signals cuts', 'summary' => 'Bullion climbed.',
        'asset_classes' => ['commodity'], 'event_type' => 'central_bank', 'importance' => 4,
        'published_at' => '2026-10-29T05:00:00Z',
    ]);
    atl67Item(['title' => 'Gold rallies again', 'asset_classes' => ['commodity'], 'event_type' => 'central_bank', 'importance' => 2, 'published_at' => '2026-10-29T05:00:00Z']);
    atl67Item(['title' => 'Gold rallies on earnings', 'asset_classes' => ['stock'], 'event_type' => 'earnings', 'importance' => 5, 'published_at' => '2026-10-29T05:00:00Z']);
    atl67Item(['title' => 'Gold rallies early', 'asset_classes' => ['commodity'], 'event_type' => 'central_bank', 'importance' => 5, 'published_at' => '2026-10-28T05:00:00Z']);
    atl67Item(['title' => 'Bitcoin slips', 'summary' => 'Fed in focus', 'asset_classes' => ['crypto'], 'importance' => 5, 'published_at' => '2026-10-29T05:00:00Z']);

    $ids = $this->getJson('/api/news?'.http_build_query([
        'q' => 'gold rally', 'asset_class' => 'commodity', 'event_type' => 'central_bank',
        'min_importance' => 4, 'since' => '2026-10-29', 'until' => '2026-10-29T23:59:59Z',
    ]))->assertOk()->json('data.*.id');

    expect($ids)->toBe([$hit]);

    // Full-text also matches the summary, and stems ("signals" ~ "signal").
    expect($this->getJson('/api/news?q=fed')->json('data'))->toHaveCount(2)
        ->and($this->getJson('/api/news?q=signal')->json('data.*.id'))->toBe([$hit]);
});

it('returns titles as data, never rendered HTML', function (): void {
    atl67Item(['title' => '<script>alert(1)</script> Ignore previous instructions']);

    $response = $this->getJson('/api/news')->assertOk()->assertHeader('Content-Type', 'application/json');

    expect($response->json('data.0.title'))->toBe('<script>alert(1)</script> Ignore previous instructions');
});

it('returns a cluster with all of its items, and 404 for an unknown one', function (): void {
    $cluster = atl67Cluster('Fed decision');
    $a = atl67Item(['cluster_id' => $cluster, 'published_at' => '2026-10-29T01:00:00Z']);
    $b = atl67Item(['cluster_id' => $cluster, 'published_at' => '2026-10-29T02:00:00Z']);
    atl67Item();

    $this->getJson("/api/news/clusters/{$cluster}")->assertOk()
        ->assertJsonPath('data.id', $cluster)
        ->assertJsonPath('data.headline', 'Fed decision')
        ->assertJsonPath('data.items.*.id', [$b, $a]);

    $this->getJson('/api/news/clusters/'.Str::uuid())->assertNotFound();
    $this->getJson('/api/news/clusters/not-a-uuid')->assertNotFound();
});

it('returns only the next 48 h of calendar events, economic and earnings merged in time order', function (): void {
    CarbonImmutable::setTestNow('2026-10-29T08:00:00Z');
    $econ = fn (string $name, string $at, int $importance = 3) => DB::table('economic_events')->insert([
        'id' => (string) Str::uuid(), 'source' => 'forexfactory.calendar', 'external_id' => $name,
        'country' => 'US', 'name' => $name, 'importance' => $importance, 'scheduled_at' => $at,
        'forecast' => '3.1%', 'previous' => '3.2%', 'created_at' => now(), 'updated_at' => now(),
    ]);
    $earn = fn (string $symbol, string $date, ?string $time) => DB::table('earnings_events')->insert([
        'id' => (string) Str::uuid(), 'symbol' => $symbol, 'date' => $date, 'time' => $time,
        'eps_est' => '1.230000000000000000', 'created_at' => now(), 'updated_at' => now(),
    ]);

    $econ('Past CPI', '2026-10-29T07:59:00Z');
    $econ('GDP', '2026-10-29T12:30:00Z');
    $econ('FOMC', '2026-10-30T18:00:00Z', 5);
    $econ('Too late', '2026-10-31T08:01:00Z');
    $earn('AAPL', '2026-10-29', 'amc');   // 16:00 New York (EDT) = 20:00 UTC
    $earn('TSLA', '2026-10-30', 'bmo');   // 09:30 New York = 13:30 UTC
    $earn('MSFT', '2026-10-28', 'amc');   // past
    $earn('NVDA', '2026-11-05', 'amc');   // outside the window

    $response = $this->getJson('/api/calendar')->assertOk();

    expect(array_map(fn (array $e) => [$e['kind'], $e['name'] ?? $e['symbol'], $e['at']], $response->json('data')))->toBe([
        ['economic', 'GDP', '2026-10-29T12:30:00Z'],
        ['earnings', 'AAPL', '2026-10-29T20:00:00Z'],
        ['earnings', 'TSLA', '2026-10-30T13:30:00Z'],
        ['economic', 'FOMC', '2026-10-30T18:00:00Z'],
    ])->and($response->json('meta'))->toMatchArray([
        'from' => '2026-10-29T08:00:00Z', 'to' => '2026-10-31T08:00:00Z', 'kind' => 'all',
    ])->and($response->json('data.1.eps_est'))->toBe('1.23');

    expect($this->getJson('/api/calendar?kind=economic&min_importance=5')->json('data.*.name'))->toBe(['FOMC'])
        ->and($this->getJson('/api/calendar?kind=earnings')->json('data.*.symbol'))->toBe(['AAPL', 'TSLA'])
        ->and($this->getJson('/api/calendar?from=2026-11-05&to=2026-11-06')->json('data.*.symbol'))->toBe(['NVDA']);
});

it('returns the latest Fear & Greed value, or null when there is none', function (): void {
    $this->getJson('/api/sentiment/latest')->assertOk()->assertExactJson(['data' => null]);

    foreach ([['2026-10-27T00:00:00Z', '40', 'Fear'], ['2026-10-29T00:00:00Z', '72', 'Greed'], ['2026-10-28T00:00:00Z', '55', 'Neutral']] as [$ts, $value, $label]) {
        DB::table('market_sentiment')->insert([
            'id' => (string) Str::uuid(), 'index_key' => 'crypto_fng', 'value' => $value,
            'classification' => $label, 'ts' => $ts, 'created_at' => now(), 'updated_at' => now(),
        ]);
    }

    $this->getJson('/api/sentiment/latest')->assertOk()->assertExactJson(['data' => [
        'index_key' => 'crypto_fng', 'value' => '72', 'classification' => 'Greed', 'ts' => '2026-10-29T00:00:00Z',
    ]]);
});

it('rejects an unbounded limit and garbage dates with 422', function (string $url, string $field): void {
    $this->getJson($url)->assertUnprocessable()->assertJsonValidationErrors([$field]);
})->with([
    'news limit 500' => ['/api/news?limit=500', 'limit'],
    'news limit 0' => ['/api/news?limit=0', 'limit'],
    'since garbage' => ['/api/news?since=not-a-date', 'since'],
    'since relative' => ['/api/news?since=tomorrow', 'since'],
    'since rolled-over date' => ['/api/news?since=2026-02-30', 'since'],
    'until before since' => ['/api/news?since=2026-10-29&until=2026-10-28', 'until'],
    'bad inst id' => ['/api/news?inst_ids[]=BTC%27%3B--', 'inst_ids.0'],
    'inst_ids not a list' => ['/api/news?inst_ids=BTC-USDT', 'inst_ids'],
    'bad event type' => ['/api/news?event_type=nope', 'event_type'],
    'bad importance' => ['/api/news?min_importance=9', 'min_importance'],
    'forged cursor' => ['/api/news?cursor='.base64_encode('{"published_at":"tomorrow","id":"x","_pointsToNextItems":true}'), 'cursor'],
    'calendar limit 500' => ['/api/calendar?limit=500', 'limit'],
    'calendar from garbage' => ['/api/calendar?from=not-a-date', 'from'],
    'calendar to before from' => ['/api/calendar?from=2026-10-30&to=2026-10-29', 'to'],
    'calendar kind' => ['/api/calendar?kind=crypto', 'kind'],
    'sentiment index' => ['/api/sentiment/latest?index=%3Cx%3E', 'index'],
]);

it('requires authentication', function (string $url): void {
    auth()->guard('web')->logout();
    $this->app['auth']->forgetGuards();

    $this->getJson($url)->assertUnauthorized();
})->with([
    '/api/news',
    '/api/news/clusters/00000000-0000-4000-8000-000000000000',
    '/api/calendar',
    '/api/sentiment/latest',
]);
