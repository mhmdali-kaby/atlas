<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Setting;
use App\Models\SettingsHistory;
use App\Models\User;
use App\Support\Events\EventBus;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\File;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Str;

/**
 * ATL-251, brief §14.7, decision #265 (3): prompt versions list, one body, and the pin.
 */
const ATL251_PREAMBLE = "SHARED PREAMBLE v1\n";

function atl251Version(string $agent, int $version, string $body, string $createdAt): void
{
    DB::table('prompt_versions')->insert([
        'id' => (string) Str::uuid(),
        'agent' => $agent,
        'version' => $version,
        'sha256' => hash('sha256', $body),
        'body' => $body,
        'created_at' => $createdAt,
        'updated_at' => $createdAt,
    ]);
}

function atl251Run(string $agent, int $version): void
{
    DB::table('agent_runs')->insert([
        'id' => (string) Str::uuid(),
        'env' => 'demo',
        'agent' => $agent,
        'trigger' => 'mention',
        'started_at' => '2026-11-05T08:00:00Z',
        'ended_at' => '2026-11-05T08:01:00Z',
        'status' => 'ok',
        'model' => 'opus',
        'prompt_version' => $version,
        'tokens_in' => 0,
        'tokens_out' => 0,
        'turns' => 0,
        'cost_usd_notional' => '0',
        'created_at' => '2026-11-05T08:00:00Z',
        'updated_at' => '2026-11-05T08:00:00Z',
    ]);
}

/**
 * @return list<array<string, mixed>>
 */
function atl251SettingsEvents(string $settingsId): array
{
    /** @var list<array{0: string, 1: list<string>}> $entries */
    $entries = Redis::connection('events')->command('executeRaw', [['XRANGE', EventBus::streamKey('atlas.system'), '-', '+']]);
    $found = [];
    foreach ($entries as [, $fields]) {
        $map = [];
        for ($i = 0; $i + 1 < count($fields); $i += 2) {
            $map[$fields[$i]] = $fields[$i + 1];
        }
        $envelope = json_decode($map[EventBus::FIELD] ?? 'null', true);
        if (is_array($envelope) && $envelope['type'] === 'system.settings_changed'
            && ($envelope['payload']['settings_id'] ?? null) === $settingsId) {
            $found[] = $envelope;
        }
    }

    return $found;
}

beforeEach(function (): void {
    $this->owner = User::factory()->create();
    $this->actingAs($this->owner, 'web');

    // The prompt files on disk: historian's current file is version 2.
    $this->dir = storage_path('framework/testing/atl251-prompts-'.Str::random(8));
    File::ensureDirectoryExists($this->dir);
    File::put($this->dir.'/_preamble.md', ATL251_PREAMBLE);
    File::put($this->dir.'/historian.md', "ROLE: Historian v2\n");
    config(['services.agent_prompts.dir' => $this->dir]);

    atl251Version('historian', 1, ATL251_PREAMBLE."ROLE: Historian v1\n", '2026-11-01T08:00:00Z');
    atl251Version('historian', 2, ATL251_PREAMBLE."ROLE: Historian v2\n", '2026-11-02T08:00:00Z');
    atl251Version('historian', 3, ATL251_PREAMBLE."ROLE: Historian v3\n", '2026-11-03T08:00:00Z');
    atl251Version('market_scout', 1, ATL251_PREAMBLE."ROLE: Market Scout v1\n", '2026-11-01T08:00:00Z');
    atl251Version('market_scout', 4, ATL251_PREAMBLE."ROLE: Market Scout v4\n", '2026-11-04T08:00:00Z');
    atl251Run('historian', 1);
    atl251Run('historian', 1);
    atl251Run('historian', 2);
});

afterEach(function (): void {
    File::deleteDirectory($this->dir);
});

it('lists the historian versions newest first with hash12 and exactly one current file', function (): void {
    $data = $this->getJson('/api/agents/historian/prompts?env=demo')->assertOk()->json('data');

    expect(array_column($data, 'version'))->toBe([3, 2, 1])
        ->and($data[0])->toHaveKeys(['version', 'hash12', 'created_at', 'runs_count', 'is_current_file', 'is_pinned'])
        ->and($data[1]['hash12'])->toBe(substr(hash('sha256', ATL251_PREAMBLE."ROLE: Historian v2\n"), 0, 12))
        ->and(array_column($data, 'runs_count'))->toBe([0, 1, 2])
        ->and(array_column($data, 'is_current_file'))->toBe([false, true, false])
        ->and(array_column($data, 'is_pinned'))->toBe([false, false, false])
        ->and($data[2]['created_at'])->toBe('2026-11-01T08:00:00.000000Z')
        ->and(json_encode($data))->not->toContain('ROLE:');
});

it('marks no version current when the prompt file is missing', function (): void {
    File::delete($this->dir.'/historian.md');

    $data = $this->getJson('/api/agents/historian/prompts')->assertOk()->json('data');

    expect(array_filter(array_column($data, 'is_current_file')))->toBe([]);
});

it('returns a stored body, and 404 for another agent\'s version or an unknown slug', function (): void {
    $this->getJson('/api/agents/historian/prompts/1')->assertOk()->assertExactJson(['data' => [
        'version' => 1,
        'hash12' => substr(hash('sha256', ATL251_PREAMBLE."ROLE: Historian v1\n"), 0, 12),
        'body' => ATL251_PREAMBLE."ROLE: Historian v1\n",
    ]]);

    // Version 4 exists only for market_scout.
    $this->getJson('/api/agents/historian/prompts/4')->assertNotFound();
    $this->getJson('/api/agents/market_scout/prompts/4')->assertOk()->assertJsonPath('data.body', ATL251_PREAMBLE."ROLE: Market Scout v4\n");
    $this->getJson('/api/agents/nobody/prompts')->assertNotFound();
    $this->getJson('/api/agents/nobody/prompts/1')->assertNotFound();
    $this->putJson('/api/agents/nobody/prompt-pin', ['version' => 1])->assertNotFound();
});

it('pins historian to version 1 with history, audit and settings_changed', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $before = Setting::query()->where('env', 'demo')->firstOrFail();

    $response = $this->putJson('/api/agents/historian/prompt-pin', ['version' => 1, 'env' => 'demo'])->assertOk();

    $after = Setting::query()->where('env', 'demo')->firstOrFail();
    expect($after->version)->toBe($before->version + 1)
        ->and($after->data['agents.historian.prompt_version'])->toBe(1)
        ->and($response->json('data'))->toBe([
            'agent' => 'historian', 'env' => 'demo', 'pinned_version' => 1, 'settings_version' => $after->version,
        ]);

    $history = SettingsHistory::query()->where('settings_id', $after->id)->where('version', $after->version)->firstOrFail();
    expect($history->diff)->toEqual(['agents.historian.prompt_version' => ['from' => null, 'to' => 1]])
        ->and($history->changed_by)->toBe($this->owner->id);

    $audit = AuditLog::query()->where('action', 'settings.updated')->get()
        ->first(fn (AuditLog $row): bool => ($row->after['env'] ?? null) === 'demo' && ($row->after['version'] ?? null) === $after->version);
    expect($audit)->not->toBeNull()
        ->and($audit->after['data']['agents.historian.prompt_version'])->toBe(1)
        ->and($audit->before['data']['agents.historian.prompt_version'])->toBeNull();

    $events = array_values(array_filter(
        atl251SettingsEvents($after->id),
        fn (array $e): bool => $e['payload']['version'] === $after->version,
    ));
    expect($events)->toHaveCount(1)
        ->and($events[0]['payload']['changed_fields'])->toBe(['agents.historian.prompt_version']);

    $list = $this->getJson('/api/agents/historian/prompts?env=demo')->json();
    expect(array_column($list['data'], 'is_pinned'))->toBe([false, false, true])
        ->and($list['meta']['pinned_version'])->toBe(1);
    expect($this->getJson('/api/settings?env=demo')->json('data.settings')['agents.historian.prompt_version'])->toBe(1);
});

it('clears the pin with null, and refuses a version that is not the agent\'s', function (): void {
    $this->putJson('/api/agents/historian/prompt-pin', ['version' => 1, 'env' => 'demo'])->assertOk();

    $this->putJson('/api/agents/historian/prompt-pin', ['version' => null, 'env' => 'demo'])
        ->assertOk()->assertJsonPath('data.pinned_version', null);
    $setting = Setting::query()->where('env', 'demo')->firstOrFail();
    expect($setting->data['agents.historian.prompt_version'])->toBeNull();
    $versionAfterClear = $setting->version;

    $this->putJson('/api/agents/historian/prompt-pin', ['version' => 4, 'env' => 'demo'])
        ->assertUnprocessable()->assertJsonValidationErrors(['version']);
    $this->putJson('/api/agents/historian/prompt-pin', ['version' => '1', 'env' => 'demo'])
        ->assertUnprocessable()->assertJsonValidationErrors(['version']);
    $this->putJson('/api/agents/historian/prompt-pin', ['env' => 'demo'])
        ->assertUnprocessable()->assertJsonValidationErrors(['version']);

    expect(Setting::query()->where('env', 'demo')->firstOrFail()->version)->toBe($versionAfterClear);
});

it('validates agents.<slug>.prompt_version on PATCH /api/settings too', function (): void {
    $version = $this->getJson('/api/settings?env=demo')->json('data.version');

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => $version, 'changes' => ['agents.historian.prompt_version' => 4]])
        ->assertUnprocessable()->assertJsonValidationErrors(['changes.agents.historian.prompt_version']);
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => $version, 'changes' => ['agents.historian.prompt_version' => 3]])
        ->assertOk();
    expect($this->getJson('/api/settings?env=demo')->json('data.settings')['agents.historian.prompt_version'])->toBe(3);
});

it('answers versions outside the int4 column with 404 or 422, never a database error', function (): void {
    // QA round 1: 3000000000 overflowed Postgres integer (22003), 99999999999999999999 overflowed PHP int.
    foreach (['2147483648', '3000000000', '99999999999999999999', '0', '01'] as $segment) {
        $this->getJson("/api/agents/historian/prompts/{$segment}")->assertNotFound();
    }

    foreach ([2147483648, 3000000000, 0, -1] as $version) {
        $this->putJson('/api/agents/historian/prompt-pin', ['version' => $version, 'env' => 'demo'])
            ->assertUnprocessable()->assertJsonValidationErrors(['version']);
    }
    $this->call('PUT', '/api/agents/historian/prompt-pin', [], [], [], ['CONTENT_TYPE' => 'application/json', 'HTTP_ACCEPT' => 'application/json'],
        '{"version": 99999999999999999999, "env": "demo"}')
        ->assertUnprocessable()->assertJsonValidationErrors(['version']);

    $settingsVersion = $this->getJson('/api/settings?env=demo')->json('data.version');
    foreach ([2147483648, 3000000000] as $version) {
        $this->patchJson('/api/settings', ['env' => 'demo', 'version' => $settingsVersion, 'changes' => ['agents.historian.prompt_version' => $version]])
            ->assertUnprocessable()->assertJsonValidationErrors(['changes.agents.historian.prompt_version']);
    }
    expect($this->getJson('/api/settings?env=demo')->json('data.version'))->toBe($settingsVersion);
});

it('requires sign-in', function (): void {
    auth()->guard('web')->logout();

    $this->getJson('/api/agents/historian/prompts')->assertUnauthorized();
    $this->getJson('/api/agents/historian/prompts/1')->assertUnauthorized();
    $this->putJson('/api/agents/historian/prompt-pin', ['version' => 1])->assertUnauthorized();
});
