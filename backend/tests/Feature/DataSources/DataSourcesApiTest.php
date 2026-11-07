<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\NewsSource;
use App\Models\User;
use Database\Seeders\NewsSourcesSeeder;
use Illuminate\Contracts\Debug\ExceptionHandler;
use Illuminate\Foundation\Exceptions\Handler;
use Illuminate\Log\Events\MessageLogged;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Event;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Validation\ValidationException;

const ATL66_KEY = 'cp-PLAINTEXT-key-0123456789abcdWXYZ';

const ATL66_FIELDS = [
    'key', 'name', 'kind', 'category', 'url', 'enabled', 'requires_key', 'has_key', 'key_last4',
    'poll_seconds', 'status', 'consecutive_failures', 'disabled_until', 'last_success_at',
    'last_item_at', 'last_error', 'updated_at', 'provider', 'hourly_24h', 'covered_by',
    'reputable', 'official',
];

function atl66Source(string $key): NewsSource
{
    return NewsSource::query()->where('key', $key)->sole();
}

/**
 * @return array<string, array<string, mixed>>
 */
function atl66ByKey(mixed $json): array
{
    /** @var list<array<string, mixed>> $rows */
    $rows = $json;
    $out = [];
    foreach ($rows as $row) {
        $out[(string) $row['key']] = $row;
    }

    return $out;
}

beforeEach(function (): void {
    $this->seed(NewsSourcesSeeder::class);
    $this->owner = User::factory()->create();
    $this->actingAs($this->owner, 'web');
    RateLimiter::clear('data-sources-write|'.$this->owner->id);
});

it('lists every seeded source with the documented fields', function (): void {
    $response = $this->getJson('/api/data-sources')->assertOk();

    $seeded = array_keys((array) config('atlas_defaults.news.sources'));
    $rows = atl66ByKey($response->json('data'));
    expect(array_keys($rows))->toEqualCanonicalizing($seeded);
    foreach ($rows as $row) {
        expect(array_keys($row))->toEqualCanonicalizing(ATL66_FIELDS);
    }

    expect($rows['rss.coindesk'])->toMatchArray([
        'kind' => 'rss', 'category' => 'crypto_news', 'enabled' => true, 'requires_key' => false,
        'has_key' => false, 'key_last4' => null, 'status' => 'never_run', 'poll_seconds' => 60,
    ]);

    expect(array_keys($response->json('summary')))->toEqualCanonicalizing(NewsSource::CATEGORIES);
});

it('derives degraded, needs_key, disabled, never_run and ok', function (): void {
    atl66Source('rss.coindesk')->forceFill([
        'disabled_until' => now()->addMinutes(20), 'consecutive_failures' => 3, 'last_error' => 'HTTP 503',
    ])->save();
    atl66Source('rss.cointelegraph')->forceFill([
        'disabled_until' => now()->subMinute(), 'consecutive_failures' => 0, 'last_success_at' => now(),
    ])->save();
    atl66Source('rss.decrypt')->forceFill(['enabled' => false])->save();
    atl66Source('finnhub.news')->forceFill(['api_key' => 'fh-key-1234'])->save();

    $rows = atl66ByKey($this->getJson('/api/data-sources')->assertOk()->json('data'));

    expect($rows['rss.coindesk']['status'])->toBe('degraded')
        ->and($rows['rss.coindesk']['consecutive_failures'])->toBe(3)
        ->and($rows['rss.coindesk']['disabled_until'])->not->toBeNull()
        ->and($rows['rss.coindesk']['last_error'])->toBe('HTTP 503')
        ->and($rows['rss.cointelegraph']['status'])->toBe('ok')
        ->and($rows['cryptopanic']['status'])->toBe('needs_key')
        ->and($rows['cryptopanic']['has_key'])->toBeFalse()
        ->and($rows['rss.decrypt']['status'])->toBe('disabled')
        ->and($rows['finnhub.news']['status'])->toBe('never_run')
        ->and($rows['finnhub.news']['has_key'])->toBeTrue()
        ->and($rows['finnhub.news']['key_last4'])->toBe('1234');
});

it('summarises whether every source in a category is down', function (): void {
    // Calendar: finnhub.calendar has no key; take ForexFactory down too.
    atl66Source('forexfactory.calendar')->forceFill(['disabled_until' => now()->addMinutes(5)])->save();
    // Crypto news: cryptopanic has no key, disable the RSS feeds except one.
    foreach (['rss.coindesk', 'rss.cointelegraph', 'rss.theblock'] as $key) {
        atl66Source($key)->forceFill(['enabled' => false])->save();
    }

    $summary = $this->getJson('/api/data-sources')->assertOk()->json('summary');

    expect($summary['calendar'])->toBe(['total' => 2, 'available' => 0, 'all_down' => true])
        ->and($summary['crypto_news'])->toBe(['total' => 5, 'available' => 1, 'all_down' => false])
        ->and($summary['markets_news']['all_down'])->toBeFalse();
});

it('sets a key, shows only its last 4 characters, and never leaks it', function (): void {
    /** @var list<string> $logged */
    $logged = [];
    Event::listen(MessageLogged::class, function (MessageLogged $e) use (&$logged): void {
        $logged[] = $e->message.' '.json_encode($e->context, JSON_PARTIAL_OUTPUT_ON_ERROR);
    });
    $handler = app(ExceptionHandler::class);
    assert($handler instanceof Handler);
    $handler->stopIgnoring(ValidationException::class);

    atl66Source('cryptopanic')->forceFill([
        'consecutive_failures' => 3, 'disabled_until' => now()->addMinutes(20), 'last_error' => 'HTTP 401',
    ])->save();

    $put = $this->putJson('/api/data-sources/cryptopanic/key', ['api_key' => '  '.ATL66_KEY.'  '])
        ->assertOk()
        ->assertJsonPath('data.has_key', true)
        ->assertJsonPath('data.key_last4', 'WXYZ')
        ->assertJsonPath('data.consecutive_failures', 0)
        ->assertJsonPath('data.disabled_until', null)
        ->assertJsonPath('data.last_error', null)
        ->assertJsonPath('data.status', 'never_run');

    $get = $this->getJson('/api/data-sources')->assertOk();
    $row = atl66ByKey($get->json('data'))['cryptopanic'];
    expect($row['has_key'])->toBeTrue()->and($row['key_last4'])->toBe('WXYZ');

    // Stored trimmed and encrypted; decrypts to the key.
    expect(atl66Source('cryptopanic')->api_key)->toBe(ATL66_KEY)
        ->and((string) DB::table('news_sources')->where('key', 'cryptopanic')->value('api_key'))->not->toContain(ATL66_KEY);

    // A rejected key (whitespace inside) runs the reporting path; it must not echo either.
    $bad = $this->putJson('/api/data-sources/cryptopanic/key', ['api_key' => 'abc '.ATL66_KEY])
        ->assertUnprocessable()->assertJsonValidationErrors(['api_key']);

    $this->deleteJson('/api/data-sources/cryptopanic/key')->assertNoContent();

    $audit = json_encode(DB::select('select action, before::text as b, after::text as a from audit_log'), JSON_THROW_ON_ERROR);
    foreach ([(string) $put->getContent(), (string) $get->getContent(), (string) $bad->getContent(), $audit, ...$logged] as $haystack) {
        expect($haystack)->not->toContain(ATL66_KEY)->not->toContain('abc '.ATL66_KEY);
    }
    expect($logged)->not->toBeEmpty();
});

it('removes a key, after which the source needs one again', function (): void {
    atl66Source('finnhub.news')->forceFill(['api_key' => 'fh-key-1234'])->save();

    $this->deleteJson('/api/data-sources/finnhub.news/key')->assertNoContent();

    $row = atl66ByKey($this->getJson('/api/data-sources')->json('data'))['finnhub.news'];
    expect($row)->toMatchArray(['has_key' => false, 'key_last4' => null, 'status' => 'needs_key']);
});

it('refuses a key for a source that does not use one', function (): void {
    $this->putJson('/api/data-sources/rss.coindesk/key', ['api_key' => ATL66_KEY])
        ->assertUnprocessable()->assertJsonValidationErrors(['api_key']);
    $this->deleteJson('/api/data-sources/rss.coindesk/key')
        ->assertUnprocessable()->assertJsonValidationErrors(['api_key']);

    expect(atl66Source('rss.coindesk')->getRawOriginal('api_key'))->toBeNull()
        ->and(AuditLog::query()->count())->toBe(0);
});

it('bounds the key length', function (): void {
    $max = (int) config('atlas_defaults.data_sources_api.api_key_max_length');
    $this->putJson('/api/data-sources/cryptopanic/key', ['api_key' => str_repeat('k', $max + 1)])
        ->assertUnprocessable()->assertJsonValidationErrors(['api_key']);
    $this->putJson('/api/data-sources/cryptopanic/key', ['api_key' => 'abc'])
        ->assertUnprocessable()->assertJsonValidationErrors(['api_key']);
    $this->putJson('/api/data-sources/cryptopanic/key', ['api_key' => '    '])
        ->assertUnprocessable()->assertJsonValidationErrors(['api_key']);
    $this->putJson('/api/data-sources/cryptopanic/key', [])
        ->assertUnprocessable()->assertJsonValidationErrors(['api_key']);
});

it('toggles a source and sets its poll interval', function (): void {
    $this->patchJson('/api/data-sources/rss.coindesk', ['enabled' => false, 'poll_seconds' => 600])
        ->assertOk()
        ->assertJsonPath('data.enabled', false)
        ->assertJsonPath('data.poll_seconds', 600)
        ->assertJsonPath('data.status', 'disabled');

    $source = atl66Source('rss.coindesk');
    expect($source->enabled)->toBeFalse()->and($source->poll_seconds)->toBe(600);
});

it('rejects a poll interval outside the bounds and an empty or key-carrying patch', function (): void {
    $min = (int) config('atlas_defaults.data_sources_api.poll_seconds_min');
    $max = (int) config('atlas_defaults.data_sources_api.poll_seconds_max');

    $this->patchJson('/api/data-sources/rss.coindesk', ['poll_seconds' => $min - 1])
        ->assertUnprocessable()->assertJsonValidationErrors(['poll_seconds']);
    $this->patchJson('/api/data-sources/rss.coindesk', ['poll_seconds' => $max + 1])
        ->assertUnprocessable()->assertJsonValidationErrors(['poll_seconds']);
    $this->patchJson('/api/data-sources/rss.coindesk', ['poll_seconds' => 'often'])
        ->assertUnprocessable()->assertJsonValidationErrors(['poll_seconds']);
    $this->patchJson('/api/data-sources/rss.coindesk', [])->assertUnprocessable();
    $this->patchJson('/api/data-sources/cryptopanic', ['enabled' => true, 'api_key' => ATL66_KEY])
        ->assertUnprocessable()->assertJsonValidationErrors(['api_key']);

    $this->patchJson('/api/data-sources/rss.coindesk', ['poll_seconds' => $min])->assertOk();
    $this->patchJson('/api/data-sources/rss.coindesk', ['poll_seconds' => $max])->assertOk();
    expect(atl66Source('cryptopanic')->getRawOriginal('api_key'))->toBeNull();
});

it('returns 404 for an unknown source', function (): void {
    $this->patchJson('/api/data-sources/nope.feed', ['enabled' => false])->assertNotFound();
    $this->putJson('/api/data-sources/nope.feed/key', ['api_key' => ATL66_KEY])->assertNotFound();
});

it('requires authentication', function (): void {
    auth()->guard('web')->logout();

    $this->getJson('/api/data-sources')->assertUnauthorized();
    $this->patchJson('/api/data-sources/rss.coindesk', ['enabled' => false])->assertUnauthorized();
    $this->putJson('/api/data-sources/cryptopanic/key', ['api_key' => ATL66_KEY])->assertUnauthorized();
    $this->deleteJson('/api/data-sources/cryptopanic/key')->assertUnauthorized();

    expect(atl66Source('rss.coindesk')->enabled)->toBeTrue()
        ->and(atl66Source('cryptopanic')->getRawOriginal('api_key'))->toBeNull();
});

it('writes one audit row per write naming the source and action', function (): void {
    $this->patchJson('/api/data-sources/rss.coindesk', ['enabled' => false])->assertOk();
    $this->putJson('/api/data-sources/cryptopanic/key', ['api_key' => ATL66_KEY])->assertOk();
    $this->putJson('/api/data-sources/cryptopanic/key', ['api_key' => ATL66_KEY.'2'])->assertOk();
    $this->deleteJson('/api/data-sources/cryptopanic/key')->assertNoContent();

    $rows = AuditLog::query()->where('subject_type', 'news_sources')->orderBy('created_at')->orderBy('id')->get();
    expect($rows->pluck('action')->all())->toBe([
        'news_source.updated', 'news_source.key_set', 'news_source.key_set', 'news_source.key_removed',
    ])->and(AuditLog::query()->count())->toBe(4);

    [$updated, $set, $replaced, $removed] = $rows->all();
    expect($updated->subject_id)->toBe(atl66Source('rss.coindesk')->id)
        ->and($updated->before)->toBe(['source' => 'rss.coindesk', 'enabled' => true, 'poll_seconds' => 60])
        ->and($updated->after)->toBe(['source' => 'rss.coindesk', 'enabled' => false, 'poll_seconds' => 60])
        ->and($updated->user_id)->toBe($this->owner->id)
        ->and($set->after)->toEqual(['source' => 'cryptopanic', 'provider' => 'cryptopanic', 'sources' => ['cryptopanic'], 'api_key' => 'key_set'])
        ->and($set->before)->toEqual(['source' => 'cryptopanic', 'provider' => 'cryptopanic', 'sources' => ['cryptopanic'], 'api_key' => null])
        ->and($replaced->before)->toEqual(['source' => 'cryptopanic', 'provider' => 'cryptopanic', 'sources' => ['cryptopanic'], 'api_key' => 'key_set'])
        ->and($removed->after)->toEqual(['source' => 'cryptopanic', 'provider' => 'cryptopanic', 'sources' => ['cryptopanic'], 'api_key' => 'key_removed']);

    // A no-op patch is not a write.
    $this->patchJson('/api/data-sources/rss.coindesk', ['enabled' => false])->assertOk();
    expect(AuditLog::query()->count())->toBe(4);
});

it('throttles writes per user', function (): void {
    config(['atlas_defaults.data_sources_api.writes_per_minute' => 2]);

    $this->patchJson('/api/data-sources/rss.coindesk', ['enabled' => false])->assertOk();
    $this->patchJson('/api/data-sources/rss.coindesk', ['enabled' => true])->assertOk();
    $this->patchJson('/api/data-sources/rss.coindesk', ['enabled' => false])->assertTooManyRequests();
    $this->getJson('/api/data-sources')->assertOk();
});
