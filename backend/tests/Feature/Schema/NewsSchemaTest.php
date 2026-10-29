<?php

declare(strict_types=1);

use App\Models\NewsSource;
use Database\Seeders\NewsSourcesSeeder;
use Illuminate\Database\QueryException;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Support\Facades\Crypt;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

const NEWS_TABLES = [
    'news_sources', 'news_items', 'news_clusters', 'economic_events', 'earnings_events',
    'market_sentiment', 'instrument_aliases',
];

/**
 * @param  array<string, mixed>  $overrides
 * @return array<string, mixed>
 */
function newsItem(array $overrides = []): array
{
    return [
        'id' => (string) Str::uuid(),
        'source' => 'rss.coindesk',
        'url' => 'https://www.coindesk.com/markets/2026/10/29/btc?utm_source=rss',
        'canonical_url' => 'https://www.coindesk.com/markets/2026/10/29/btc',
        'title' => 'Bitcoin climbs',
        'published_at' => '2026-10-29 08:00:00+00',
        'fetched_at' => '2026-10-29 08:01:00+00',
        'importance' => 3,
        ...$overrides,
    ];
}

it('creates all seven news tables', function (): void {
    $found = collect(DB::select(
        'SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ANY (?)',
        ['{'.implode(',', NEWS_TABLES).'}'],
    ))->pluck('table_name')->sort()->values()->all();

    expect($found)->toEqual(collect(NEWS_TABLES)->sort()->values()->all());
});

it('makes news_items.embedding a vector(384) with an HNSW cosine index and instruments a GIN-indexed text[]', function (): void {
    $types = collect(DB::select(
        "SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS type FROM pg_attribute a
         WHERE a.attrelid = 'news_items'::regclass AND a.attname IN ('embedding', 'instruments', 'asset_classes')",
    ))->pluck('type', 'attname');

    expect($types['embedding'])->toBe('vector(384)')
        ->and($types['instruments'])->toBe('text[]')
        ->and($types['asset_classes'])->toBe('text[]');

    $indexes = collect(DB::select("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'news_items'"))
        ->pluck('indexdef', 'indexname');

    expect($indexes['news_items_embedding_hnsw'] ?? '')->toContain('USING hnsw (embedding vector_cosine_ops)')
        ->and($indexes['news_items_instruments_gin'] ?? '')->toContain('USING gin (instruments)')
        ->and($indexes['news_items_published_at_index'] ?? '')->toContain('USING btree (published_at)')
        ->and($indexes['news_items_canonical_url_hash_unique'] ?? '')->toContain('UNIQUE');
});

it('seeds exactly one row per configured source key, idempotently', function (): void {
    /** @var array<string, mixed> $sources */
    $sources = config('atlas_defaults.news.sources');
    $expected = array_keys($sources);

    $this->seed(NewsSourcesSeeder::class);
    expect(NewsSource::query()->pluck('key')->sort()->values()->all())->toEqual(collect($expected)->sort()->values()->all());

    foreach (['rss.coindesk', 'cryptopanic', 'finnhub.news', 'finnhub.calendar', 'finnhub.earnings',
        'forexfactory.calendar', 'okx.announcements', 'alternative_me.fng'] as $key) {
        expect($expected)->toContain($key);
    }

    NewsSource::query()->where('key', 'rss.coindesk')->update(['enabled' => false]);
    $this->seed(NewsSourcesSeeder::class);

    expect(NewsSource::query()->count())->toBe(count($expected))
        ->and(NewsSource::query()->where('key', 'rss.coindesk')->value('enabled'))->toBeFalse();

    $cryptopanic = NewsSource::query()->where('key', 'cryptopanic')->firstOrFail();
    expect($cryptopanic->requires_key)->toBeTrue()
        ->and($cryptopanic->kind)->toBe('api')
        ->and($cryptopanic->poll_seconds)->toBe(180);
});

it('stores news_sources.api_key as ciphertext and returns the plaintext', function (): void {
    $this->seed(NewsSourcesSeeder::class);
    $source = NewsSource::query()->where('key', 'finnhub.news')->firstOrFail();
    $source->api_key = 'fh-secret-plaintext-1234';
    $source->save();

    $raw = DB::table('news_sources')->where('key', 'finnhub.news')->value('api_key');

    expect($raw)->toBeString()->not->toContain('fh-secret-plaintext-1234')
        ->and(Crypt::decryptString($raw))->toBe('fh-secret-plaintext-1234')
        ->and($source->fresh()?->api_key)->toBe('fh-secret-plaintext-1234')
        ->and($source->apiKeyLast4())->toBe('1234')
        ->and($source->toArray())->not->toHaveKey('api_key');
});

it('rejects two news_items with the same canonical_url', function (): void {
    DB::table('news_items')->insert(newsItem());

    expect(fn () => DB::table('news_items')->insert(newsItem(['source' => 'rss.cointelegraph', 'url' => 'https://x.test/other'])))
        ->toThrow(UniqueConstraintViolationException::class);
});

it('rejects importance outside 1-5', function (int $importance): void {
    expect(fn () => DB::table('news_items')->insert(newsItem(['importance' => $importance])))
        ->toThrow(QueryException::class, 'news_items_importance_check');
})->with([0, 6]);

it('accepts importance 1 and 5 and stores text[] and vector values', function (): void {
    $embedding = '['.implode(',', array_fill(0, 384, '0.5')).']';
    DB::table('news_items')->insert(newsItem(['importance' => 5]));
    DB::table('news_items')->insert(newsItem([
        'canonical_url' => 'https://x.test/2',
        'importance' => 1,
        'instruments' => '{BTC-USDT-SWAP,BTC-USDT}',
        'embedding' => $embedding,
    ]));

    $hit = DB::selectOne("SELECT count(*) AS n FROM news_items WHERE instruments @> ARRAY['BTC-USDT']::text[]");
    expect($hit->n)->toBe(1);
});

it('keeps economic_events, earnings_events, market_sentiment and instrument_aliases unique on their natural keys', function (): void {
    $now = '2026-10-29 12:00:00+00';
    $rows = [
        'economic_events' => ['source' => 'forexfactory.calendar', 'external_id' => 'cpi-2026-10', 'country' => 'USD', 'name' => 'CPI m/m', 'importance' => 5, 'scheduled_at' => $now],
        'earnings_events' => ['symbol' => 'TSLA', 'date' => '2026-10-29'],
        'market_sentiment' => ['index_key' => 'crypto_fng', 'value' => '42', 'ts' => $now],
        'instrument_aliases' => ['alias' => 'Tesla', 'alias_normalized' => 'tesla', 'target_kind' => 'instrument_family', 'target' => 'TSLA-USDT'],
    ];

    foreach ($rows as $table => $row) {
        DB::table($table)->insert(['id' => (string) Str::uuid(), ...$row]);
        expect(fn () => DB::transaction(fn () => DB::table($table)->insert(['id' => (string) Str::uuid(), ...$row])))
            ->toThrow(UniqueConstraintViolationException::class);
    }
});
