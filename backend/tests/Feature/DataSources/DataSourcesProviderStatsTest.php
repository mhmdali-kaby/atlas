<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\NewsSource;
use App\Models\User;
use Carbon\CarbonImmutable;
use Database\Seeders\NewsSourcesSeeder;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\Str;

const ATL71_KEY = 'fh-PLAINTEXT-key-0123456789abcdQRST';

function atl71Source(string $key): NewsSource
{
    return NewsSource::query()->where('key', $key)->sole();
}

/**
 * @return array<string, array<string, mixed>>
 */
function atl71Rows(mixed $json): array
{
    /** @var list<array<string, mixed>> $rows */
    $rows = $json;

    return collect($rows)->keyBy('key')->all();
}

function atl71Item(string $source, CarbonImmutable $fetchedAt): void
{
    $url = 'https://example.test/'.Str::uuid();
    DB::table('news_items')->insert([
        'id' => (string) Str::uuid(),
        'source' => $source,
        'url' => $url,
        'canonical_url' => $url,
        'title' => 'Headline',
        'published_at' => $fetchedAt,
        'fetched_at' => $fetchedAt,
        'importance' => 1,
        'created_at' => $fetchedAt,
        'updated_at' => $fetchedAt,
    ]);
}

beforeEach(function (): void {
    $this->seed(NewsSourcesSeeder::class);
    $this->owner = User::factory()->create();
    $this->actingAs($this->owner, 'web');
    RateLimiter::clear('data-sources-write|'.$this->owner->id);
    $this->finnhub = ['finnhub.calendar', 'finnhub.earnings', 'finnhub.news'];
});

afterEach(function (): void {
    CarbonImmutable::setTestNow();
});

it('returns provider, hourly_24h and covered_by on every source', function (): void {
    $rows = atl71Rows($this->getJson('/api/data-sources')->assertOk()->json('data'));

    expect($rows)->not->toBeEmpty();
    foreach ($rows as $row) {
        expect($row['provider'])->toBeString()->not->toBe('')
            ->and($row['hourly_24h'])->toHaveCount(24)->each->toBeInt()
            ->and($row)->toHaveKey('covered_by');
    }

    $finnhubKeys = collect($rows)->where('provider', 'finnhub')->keys()->sort()->values()->all();
    expect($finnhubKeys)->toBe($this->finnhub)
        ->and($rows['rss.coindesk']['provider'])->toBe('coindesk')
        ->and($rows['cryptopanic']['provider'])->toBe('cryptopanic')
        ->and($rows['okx.announcements']['provider'])->toBe('okx')
        ->and($rows['forexfactory.calendar']['provider'])->toBe('forexfactory')
        ->and($rows['alternative_me.fng']['provider'])->toBe('alternative_me');

    // Single-source responses carry the same fields.
    $one = $this->patchJson('/api/data-sources/rss.coindesk', ['poll_seconds' => 600])->assertOk()->json('data');
    expect($one['provider'])->toBe('coindesk')->and($one['hourly_24h'])->toHaveCount(24)->and($one)->toHaveKey('covered_by');
});

it('stores provider as a not-null indexed column', function (): void {
    $column = DB::selectOne("SELECT is_nullable FROM information_schema.columns WHERE table_name = 'news_sources' AND column_name = 'provider'");
    $index = DB::selectOne("SELECT indexdef FROM pg_indexes WHERE tablename = 'news_sources' AND indexdef LIKE '%(provider)%'");

    expect($column->is_nullable)->toBe('NO')->and($index)->not->toBeNull();
});

it('writes a key to every source of the provider and clears it from any of them', function (): void {
    $this->putJson('/api/data-sources/finnhub.news/key', ['api_key' => ATL71_KEY])
        ->assertOk()
        ->assertJsonPath('data.has_key', true)
        ->assertJsonPath('data.key_last4', 'QRST');

    $rows = atl71Rows($this->getJson('/api/data-sources')->json('data'));
    foreach ($this->finnhub as $key) {
        expect($rows[$key]['has_key'])->toBeTrue()->and($rows[$key]['key_last4'])->toBe('QRST');
    }
    expect($rows['cryptopanic']['has_key'])->toBeFalse();

    $raw = DB::table('news_sources')->where('provider', 'finnhub')->pluck('api_key')->unique();
    expect($raw)->toHaveCount(1)->and($raw->first())->not->toContain(ATL71_KEY);

    $this->deleteJson('/api/data-sources/finnhub.earnings/key')->assertNoContent();
    $rows = atl71Rows($this->getJson('/api/data-sources')->json('data'));
    foreach ($this->finnhub as $key) {
        expect($rows[$key]['has_key'])->toBeFalse()
            ->and($rows[$key]['key_last4'])->toBeNull()
            ->and($rows[$key]['status'])->toBe('needs_key');
    }
});

it('resets failure columns on every source of the provider', function (): void {
    foreach ($this->finnhub as $key) {
        atl71Source($key)->forceFill([
            'consecutive_failures' => 3, 'disabled_until' => now()->addMinutes(20), 'last_error' => 'HTTP 401',
        ])->save();
    }

    $this->putJson('/api/data-sources/finnhub.calendar/key', ['api_key' => ATL71_KEY])->assertOk();

    foreach ($this->finnhub as $key) {
        $s = atl71Source($key);
        expect([$s->consecutive_failures, $s->disabled_until, $s->last_error])->toBe([0, null, null]);
    }
});

it('writes one audit row per provider write, naming the provider and sources, never the key', function (): void {
    $this->putJson('/api/data-sources/finnhub.news/key', ['api_key' => ATL71_KEY])->assertOk();
    $this->deleteJson('/api/data-sources/finnhub.calendar/key')->assertNoContent();
    // Nothing left to remove: no write, no audit row.
    $this->deleteJson('/api/data-sources/finnhub.earnings/key')->assertNoContent();

    $rows = AuditLog::query()->where('subject_type', 'news_sources')->orderBy('created_at')->orderBy('id')->get();
    expect($rows->pluck('action')->all())->toBe(['news_source.key_set', 'news_source.key_removed']);

    [$set, $removed] = $rows->all();
    expect($set->subject_id)->toBe(atl71Source('finnhub.news')->id)
        ->and($set->before)->toEqual(['source' => 'finnhub.news', 'provider' => 'finnhub', 'sources' => $this->finnhub, 'api_key' => null])
        ->and($set->after)->toEqual(['source' => 'finnhub.news', 'provider' => 'finnhub', 'sources' => $this->finnhub, 'api_key' => 'key_set'])
        ->and($removed->after)->toEqual(['source' => 'finnhub.calendar', 'provider' => 'finnhub', 'sources' => $this->finnhub, 'api_key' => 'key_removed']);

    $dump = json_encode(AuditLog::query()->get()->toArray(), JSON_THROW_ON_ERROR);
    expect($dump)->not->toContain(ATL71_KEY)->not->toContain('QRST');
});

it('buckets headlines by fetched hour over the last 24 hours', function (): void {
    $now = CarbonImmutable::parse('2026-10-29T10:20:00Z');
    CarbonImmutable::setTestNow($now);
    $this->travelTo($now);

    atl71Item('rss.coindesk', $now);
    atl71Item('rss.coindesk', $now->subHours(3));
    atl71Item('rss.coindesk', $now->subHours(30));
    // 11:00Z the day before opens the window (slot 0); one second earlier is outside it.
    atl71Item('rss.decrypt', $now->startOfHour()->subHours(23));
    atl71Item('rss.decrypt', $now->startOfHour()->subHours(23)->subSecond());

    $rows = atl71Rows($this->getJson('/api/data-sources')->assertOk()->json('data'));

    $expected = array_fill(0, 24, 0);
    $expected[23] = 1;
    $expected[20] = 1;
    expect($rows['rss.coindesk']['hourly_24h'])->toBe($expected);

    $decrypt = array_fill(0, 24, 0);
    $decrypt[0] = 1;
    expect($rows['rss.decrypt']['hourly_24h'])->toBe($decrypt)
        ->and($rows['rss.cnbc']['hourly_24h'])->toBe(array_fill(0, 24, 0));
});

it('lists sources with a fixed number of queries regardless of source count', function (): void {
    atl71Item('rss.coindesk', CarbonImmutable::now());

    $count = function (): int {
        DB::flushQueryLog();
        DB::enableQueryLog();
        $this->getJson('/api/data-sources')->assertOk();
        $n = count(DB::getQueryLog());
        DB::disableQueryLog();

        return $n;
    };

    $before = $count();
    foreach (range(1, 10) as $i) {
        NewsSource::query()->create([
            'key' => "rss.extra{$i}", 'provider' => "extra{$i}", 'kind' => 'rss', 'category' => 'crypto_news',
            'name' => "Extra {$i}", 'url' => "https://extra{$i}.test/rss", 'poll_seconds' => 180, 'requires_key' => false,
        ]);
        atl71Item("rss.extra{$i}", CarbonImmutable::now());
    }

    expect($count())->toBe($before);
});

it('reports ForexFactory as covered by the Finnhub calendar only while it is enabled and keyed', function (): void {
    $coveredBy = fn (): mixed => atl71Rows($this->getJson('/api/data-sources')->json('data'))['forexfactory.calendar']['covered_by'];

    expect($coveredBy())->toBeNull();

    $this->putJson('/api/data-sources/finnhub.news/key', ['api_key' => ATL71_KEY])->assertOk();
    expect($coveredBy())->toBe('finnhub.calendar')
        ->and(atl71Rows($this->getJson('/api/data-sources')->json('data'))['finnhub.calendar']['covered_by'])->toBeNull();

    $this->patchJson('/api/data-sources/finnhub.calendar', ['enabled' => false])->assertOk();
    expect($coveredBy())->toBeNull();

    $this->patchJson('/api/data-sources/finnhub.calendar', ['enabled' => true])->assertOk();
    expect($coveredBy())->toBe('finnhub.calendar');

    $this->deleteJson('/api/data-sources/finnhub.news/key')->assertNoContent();
    expect($coveredBy())->toBeNull();
});
