<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\User;
use App\Support\Events\EventBus;
use Illuminate\Support\Facades\Redis;

/*
 * ATL-163: POST /api/cycles (the composer's /cycle now) publishes cycle.start_requested on
 * atlas.system and orchestrator.cycle_now on atlas.warroom (the Orchestrator's consumer, ATL-160),
 * writes one audit row, and allows one request per user per minute.
 */

/** @return list<array<string, mixed>> */
function atl163Stream(string $stream): array
{
    $entries = Redis::connection('events')->command('executeRaw', [['XRANGE', EventBus::streamKey($stream), '-', '+']]);

    return array_values(array_map(
        fn (array $e): array => json_decode($e[1][1], true, flags: JSON_THROW_ON_ERROR),
        is_array($entries) ? $entries : [],
    ));
}

beforeEach(function (): void {
    config(['atlas_defaults.env.default' => 'demo']);
    Redis::connection('events')->command('executeRaw', [['DEL', EventBus::streamKey('atlas.system')]]);
    Redis::connection('events')->command('executeRaw', [['DEL', EventBus::streamKey('atlas.warroom')]]);
    $this->user = User::factory()->create();
});

it('publishes cycle.start_requested on atlas.system, starts the orchestrator and writes an audit row', function (): void {
    $response = $this->actingAs($this->user, 'web')->postJson('/api/cycles')->assertStatus(202);
    $requestId = $response->json('data.request_id');

    $system = atl163Stream('atlas.system');
    expect($system)->toHaveCount(1)
        ->and($system[0]['type'])->toBe('cycle.start_requested')
        ->and($system[0]['env'])->toBe('demo')
        ->and($system[0]['source'])->toBe('human:owner')
        ->and($system[0]['payload'])->toMatchArray(['request_id' => $requestId, 'kind' => 'manual', 'requested_by' => (string) $this->user->id])
        ->and($response->json('data.event_id'))->toBe($system[0]['id']);

    $warroom = atl163Stream('atlas.warroom');
    expect($warroom)->toHaveCount(1)
        ->and($warroom[0]['type'])->toBe('orchestrator.cycle_now')
        ->and($warroom[0]['correlation_id'])->toBe($system[0]['correlation_id']);

    $audit = AuditLog::query()->where('action', 'cycle.start_requested')->sole();
    expect($audit->subject_id)->toBe($requestId)
        ->and($audit->user_id)->toBe($this->user->id);
});

it('answers 429 to a second request within 60 s and publishes nothing for it', function (): void {
    $this->actingAs($this->user, 'web')->postJson('/api/cycles')->assertStatus(202);
    $this->actingAs($this->user, 'web')->postJson('/api/cycles')->assertStatus(429);

    expect(atl163Stream('atlas.system'))->toHaveCount(1)
        ->and(AuditLog::query()->where('action', 'cycle.start_requested')->count())->toBe(1);

    $this->travel(61)->seconds();
    $this->actingAs($this->user, 'web')->postJson('/api/cycles')->assertStatus(202);
    expect(atl163Stream('atlas.system'))->toHaveCount(2);
});

it('refuses a guest with 401', function (): void {
    $this->postJson('/api/cycles')->assertUnauthorized();
    expect(atl163Stream('atlas.system'))->toBe([]);
});
