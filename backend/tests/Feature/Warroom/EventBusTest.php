<?php

declare(strict_types=1);

use App\Support\Events\EventBus;
use Illuminate\Support\Facades\Redis;

/**
 * @return list<array{id: string, fields: array<string, string>}>
 */
function streamEntries(string $stream): array
{
    /** @var list<array{0: string, 1: list<string>}> $entries */
    $entries = Redis::connection('events')->command('executeRaw', [['XRANGE', $stream, '-', '+']]);

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
    Redis::connection('events')->command('executeRaw', [['DEL', 'atlas.warroom']]);
});

it('publishes one envelope entry on atlas.warroom', function (): void {
    $published = app(EventBus::class)->publish(
        'atlas.warroom', 'warroom.message', 'demo', 'human:owner', ['content_md' => 'hello', 'n' => 1.0], 'cycle-2026-10-29',
    );

    $entries = streamEntries('atlas.warroom');
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

    $length = Redis::connection('events')->command('executeRaw', [['XLEN', 'atlas.warroom']]);
    // MAXLEN ~ trims whole macro-nodes (100 entries by default), so the stream stays well under 400.
    expect($length)->toBeLessThan(400)->toBeGreaterThanOrEqual(10);
});

it('refuses a stream outside the seven in the brief', function (): void {
    app(EventBus::class)->publish('atlas.nope', 'warroom.message', 'demo', 'test', []);
})->throws(InvalidArgumentException::class, 'Unknown event stream [atlas.nope].');
