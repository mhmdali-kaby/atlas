<?php

declare(strict_types=1);

use App\Support\Events\EventBus;
use Illuminate\Support\Facades\Redis;
use Tests\Support\TestRedis;

/**
 * @param  string  $key  the stream's physical key (EventBus::streamKey()), not the logical name
 * @return list<array{id: string, fields: array<string, string>}>
 */
function streamEntries(string $key): array
{
    /** @var list<array{0: string, 1: list<string>}> $entries */
    $entries = Redis::connection('events')->command('executeRaw', [['XRANGE', $key, '-', '+']]);

    $out = [];
    foreach ($entries as [$id, $fields]) {
        $map = [];
        for ($i = 0; $i + 1 < count($fields); $i += 2) {
            $map[$fields[$i]] = $fields[$i + 1];
        }
        $out[] = ['id' => $id, 'fields' => $map];
    }

    return $out;
}

beforeEach(function (): void {
    Redis::connection('events')->command('executeRaw', [['DEL', EventBus::streamKey('atlas.warroom')]]);
});

it('publishes one envelope entry on atlas.warroom', function (): void {
    $published = app(EventBus::class)->publish(
        'atlas.warroom', 'warroom.message', 'demo', 'human:owner', ['content_md' => 'hello', 'n' => 1.0], 'cycle-2026-10-29',
    );

    $entries = streamEntries(EventBus::streamKey('atlas.warroom'));
    expect($entries)->toHaveCount(1)
        ->and(array_keys($entries[0]['fields']))->toBe([EventBus::FIELD]);

    $envelope = json_decode($entries[0]['fields'][EventBus::FIELD], true, flags: JSON_THROW_ON_ERROR);
    expect(array_keys($envelope))->toBe(['id', 'type', 'ts', 'env', 'source', 'correlation_id', 'payload'])
        ->and($envelope)->toBe($published)
        ->and($envelope['id'])->toMatch('/^[0-9a-f-]{36}$/')
        ->and($envelope['type'])->toBe('warroom.message')
        ->and($envelope['ts'])->toMatch('/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/')
        ->and($envelope['env'])->toBe('demo')
        ->and($envelope['source'])->toBe('human:owner')
        ->and($envelope['correlation_id'])->toBe('cycle-2026-10-29')
        ->and($envelope['payload'])->toBe(['content_md' => 'hello', 'n' => 1.0]);
});

it('trims the stream to about the configured maximum length', function (): void {
    config()->set('atlas_defaults.event_bus.stream_maxlen', 10);
    $bus = app(EventBus::class);
    for ($i = 0; $i < 400; $i++) {
        $bus->publish('atlas.warroom', 'warroom.message', 'demo', 'test', ['i' => $i]);
    }

    $length = Redis::connection('events')->command('executeRaw', [['XLEN', EventBus::streamKey('atlas.warroom')]]);
    // MAXLEN ~ trims whole macro-nodes (100 entries by default), so the stream stays well under 400.
    expect($length)->toBeLessThan(400)->toBeGreaterThanOrEqual(10);
});

it('refuses a stream outside the seven in the brief', function (): void {
    app(EventBus::class)->publish('atlas.nope', 'warroom.message', 'demo', 'test', []);
})->throws(InvalidArgumentException::class, 'Unknown event stream [atlas.nope].');

/**
 * ATL-211: under the test bootstrap, every checkout gets its own Redis key prefix
 * (`test_<8 hex>:`), the same 8 hex as its Postgres test database (#252). EventBus must write
 * to that prefixed key, not the plain `atlas.warroom` every other checkout also shares.
 */
it('publishes to this checkout\'s prefixed stream key, not the plain atlas.warroom', function (): void {
    Redis::connection('events')->command('executeRaw', [['DEL', EventBus::streamKey('atlas.warroom')]]);

    app(EventBus::class)->publish('atlas.warroom', 'warroom.message', 'demo', 'test', []);

    $prefix = TestRedis::prefix();
    expect($prefix)->toMatch('/^test_[0-9a-f]{8}:$/')
        ->and(EventBus::streamKey('atlas.warroom'))->toBe($prefix.'atlas.warroom')
        ->and(Redis::connection('events')->command('executeRaw', [['XLEN', EventBus::streamKey('atlas.warroom')]]))->toBe(1);
});
