<?php

declare(strict_types=1);

use App\Models\User;
use Carbon\CarbonImmutable;
use Illuminate\Process\FakeProcessResult;
use Illuminate\Process\PendingProcess;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Process;
use Illuminate\Support\Str;

/** ATL-113: never a real value; it must never appear in any response. */
const ATL113_API_KEY = 'sk-ant-atl113-test-only-key-value-0123456789';

const ATL113_EMAIL = 'owner.atl113@example.com';

const ATL113_ROSTER = [
    'orchestrator', 'market_scout', 'news_intelligence', 'historian', 'technical_analyst',
    'derivatives_sentiment', 'strategist', 'devils_advocate', 'portfolio_manager',
];

/**
 * @param  array<string, mixed>  $overrides
 */
function atl113Run(string $agent, string $startedAt, array $overrides = []): string
{
    $id = (string) Str::uuid();
    DB::table('agent_runs')->insert([
        'id' => $id,
        'env' => 'demo',
        'agent' => $agent,
        'trigger' => 'mention',
        'started_at' => $startedAt,
        'ended_at' => $startedAt,
        'status' => 'ok',
        'model' => 'opus',
        'tokens_in' => 0,
        'tokens_out' => 0,
        'turns' => 0,
        'cost_usd_notional' => '0',
        'created_at' => $startedAt,
        'updated_at' => $startedAt,
        ...$overrides,
    ]);

    return $id;
}

/**
 * Fakes the `claude` CLI: installed, and `auth status --json` returns $status (the real CLI
 * includes the account email there).
 *
 * @param  array<string, mixed>  $status
 */
function atl113FakeClaude(array $status, int $versionExit = 0): void
{
    Process::fake(function (PendingProcess $process) use ($status, $versionExit) {
        $command = is_array($process->command) ? $process->command : [];
        if (in_array('--version', $command, true)) {
            return Process::result(output: "2.4.1 (Claude Code)\n", exitCode: $versionExit);
        }
        if (in_array('status', $command, true)) {
            return Process::result(output: (string) json_encode($status));
        }

        return Process::result(output: 'ok '.ATL113_EMAIL, exitCode: 0);
    });
}

function atl113ClearAnthropicEnv(): void
{
    putenv('ANTHROPIC_API_KEY');
    putenv('ANTHROPIC_AUTH_TOKEN');
    unset($_ENV['ANTHROPIC_API_KEY'], $_ENV['ANTHROPIC_AUTH_TOKEN'], $_SERVER['ANTHROPIC_API_KEY'], $_SERVER['ANTHROPIC_AUTH_TOKEN']);
}

beforeEach(function (): void {
    atl113ClearAnthropicEnv();
});

afterEach(function (): void {
    atl113ClearAnthropicEnv();
    CarbonImmutable::setTestNow();
});

it('refuses unauthenticated requests with 401', function (): void {
    $this->getJson('/api/agents')->assertUnauthorized();
    $this->getJson('/api/agents/claude-status')->assertUnauthorized();
});

it('lists the nine roster agents with settings, state, prompt and last run', function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    CarbonImmutable::setTestNow('2026-10-30T12:00:00Z');

    DB::table('agent_states')->insert([
        'id' => (string) Str::uuid(), 'agent' => 'historian', 'env' => 'demo', 'state' => 'thinking',
        'current_task' => 'Regime table for the shortlist', 'state_since' => '2026-10-30T11:59:00Z',
        'created_at' => now(), 'updated_at' => now(),
    ]);
    // A state row for a disabled agent is still reported as disabled.
    DB::table('agent_states')->insert([
        'id' => (string) Str::uuid(), 'agent' => 'strategist', 'env' => 'demo', 'state' => 'idle',
        'current_task' => 'stale', 'state_since' => '2026-10-30T10:00:00Z',
        'created_at' => now(), 'updated_at' => now(),
    ]);
    $sha1 = str_repeat('a', 64);
    $sha2 = 'b1c2d3e4f5a6'.str_repeat('0', 52);
    foreach ([[1, $sha1], [2, $sha2]] as [$version, $sha]) {
        DB::table('prompt_versions')->insert([
            'id' => (string) Str::uuid(), 'agent' => 'historian', 'version' => $version, 'sha256' => $sha,
            'body' => 'prompt', 'created_at' => now(), 'updated_at' => now(),
        ]);
    }
    atl113Run('historian', '2026-10-30T09:00:00Z', ['prompt_version' => 1, 'status' => 'ok']);
    $last = atl113Run('historian', '2026-10-30T11:30:00Z', [
        'prompt_version' => 2, 'status' => 'rate_limited', 'trigger' => 'schedule', 'error' => 'secret-ish detail',
    ]);

    $response = $this->getJson('/api/agents?env=demo')->assertOk();

    expect(array_column($response->json('data'), 'slug'))->toBe(ATL113_ROSTER)
        ->and($response->json('meta.env'))->toBe('demo')
        ->and($response->json('meta.day'))->toBe('2026-10-30')
        ->and($response->json('meta.timezone'))->toBe('Asia/Baghdad')
        ->and($response->json('meta.settings_version'))->toBe(1);

    foreach ($response->json('data') as $agent) {
        expect(array_keys($agent))->toBe([
            'slug', 'name', 'handle', 'enabled', 'model', 'model_name', 'state', 'state_since',
            'current_task', 'today', 'budget', 'prompt', 'last_run',
        ])
            ->and(array_keys($agent['today']))->toBe(['tokens', 'tokens_in', 'tokens_out', 'turns', 'runs', 'cost_usd_notional'])
            ->and(array_keys($agent['budget']))->toBe(['daily_tokens', 'daily_turns']);
    }

    $historian = $response->json('data.3');
    expect($historian['name'])->toBe('Historian')
        ->and($historian['handle'])->toBe('Historian')
        ->and($historian['enabled'])->toBeTrue()
        ->and($historian['model'])->toBe('strong')
        ->and($historian['model_name'])->toBe('opus')
        ->and($historian['state'])->toBe('thinking')
        ->and($historian['state_since'])->toBe('2026-10-30T11:59:00.000000Z')
        ->and($historian['current_task'])->toBe('Regime table for the shortlist')
        ->and($historian['budget'])->toBe(['daily_tokens' => 3000000, 'daily_turns' => 300])
        ->and($historian['prompt'])->toMatchArray(['sequence' => 2, 'sha' => 'b1c2d3e4f5a6', 'sha256' => $sha2])
        ->and($historian['last_run'])->toMatchArray([
            'id' => $last, 'status' => 'rate_limited', 'trigger' => 'schedule',
            'started_at' => '2026-10-30T11:30:00.000000Z', 'prompt_version' => 2,
        ])
        ->and(json_encode($historian))->not->toContain('secret-ish detail');

    $scout = $response->json('data.1');
    expect($scout['name'])->toBe('Market Scout')
        ->and($scout['handle'])->toBe('Scout')
        ->and($scout['enabled'])->toBeTrue()
        ->and($scout['state'])->toBe('idle')
        ->and($scout['prompt'])->toBeNull()
        ->and($scout['last_run'])->toBeNull()
        ->and($scout['today'])->toBe(['tokens' => 0, 'tokens_in' => 0, 'tokens_out' => 0, 'turns' => 0, 'runs' => 0, 'cost_usd_notional' => '0.000000']);

    $strategist = $response->json('data.6');
    expect($strategist['state'])->toBe('disabled')->and($strategist['current_task'])->toBeNull();
});

it('reports an enabled agent with no state row as idle and follows the settings', function (): void {
    $user = User::factory()->create();
    $this->actingAs($user, 'web');
    $this->getJson('/api/settings?env=demo')->assertOk();
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => [
        'agents.market_scout.enabled' => true,
        'agents.market_scout.model' => 'routine',
        'agents.market_scout.daily_tokens' => 1000,
        'agents.market_scout.daily_turns' => 7,
    ]])->assertOk();

    $scout = $this->getJson('/api/agents')->assertOk()->json('data.1');
    expect($scout['enabled'])->toBeTrue()
        ->and($scout['state'])->toBe('idle')
        ->and($scout['model'])->toBe('routine')
        ->and($scout['model_name'])->toBe('haiku')
        ->and($scout['budget'])->toBe(['daily_tokens' => 1000, 'daily_turns' => 7]);
});

it("sums today's tokens and turns from agent_runs for the Baghdad day only", function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    // 12:00 UTC = 15:00 Baghdad: the day is [2026-10-29T21:00Z, 2026-10-30T21:00Z).
    CarbonImmutable::setTestNow('2026-10-30T12:00:00Z');

    // In the Baghdad day.
    atl113Run('historian', '2026-10-29T21:00:00Z', ['tokens_in' => 1000, 'tokens_out' => 200, 'turns' => 3, 'cost_usd_notional' => '0.100000']);
    atl113Run('historian', '2026-10-30T20:59:59Z', ['tokens_in' => 50, 'tokens_out' => 5, 'turns' => 1, 'cost_usd_notional' => '0.012345']);
    atl113Run('historian', '2026-10-30T11:00:00Z', ['tokens_in' => 7, 'tokens_out' => 3, 'turns' => 2, 'status' => 'error']);
    // Same UTC date, but the previous Baghdad day.
    atl113Run('historian', '2026-10-29T20:59:59Z', ['tokens_in' => 999999, 'tokens_out' => 1, 'turns' => 99]);
    atl113Run('historian', '2026-10-30T02:00:00Z', ['tokens_in' => 999999, 'turns' => 99, 'env' => 'live']);
    // The next Baghdad day.
    atl113Run('historian', '2026-10-30T21:00:00Z', ['tokens_in' => 999999, 'turns' => 99]);
    // Another agent's run is counted for that agent only.
    atl113Run('news_intelligence', '2026-10-30T08:00:00Z', ['tokens_in' => 10, 'tokens_out' => 10, 'turns' => 4]);

    $data = collect($this->getJson('/api/agents?env=demo')->assertOk()->json('data'))->keyBy('slug');

    expect($data['historian']['today'])->toBe([
        'tokens' => 1265, 'tokens_in' => 1057, 'tokens_out' => 208, 'turns' => 6, 'runs' => 3,
        'cost_usd_notional' => '0.112345',
    ])
        ->and($data['news_intelligence']['today']['tokens'])->toBe(20)
        ->and($data['news_intelligence']['today']['turns'])->toBe(4)
        ->and($data['orchestrator']['today']['tokens'])->toBe(0);

    // The live env sees only its own run.
    $live = collect($this->getJson('/api/agents?env=live')->assertOk()->json('data'))->keyBy('slug');
    expect($live['historian']['today']['turns'])->toBe(99)
        ->and($live['historian']['today']['runs'])->toBe(1);
});

it('refuses an unknown env', function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    $this->getJson('/api/agents?env=paper')->assertUnprocessable()->assertJsonValidationErrors('env');
});

it('reports the claude CLI status without the API key value or an email address', function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    putenv('ANTHROPIC_API_KEY='.ATL113_API_KEY);
    $_ENV['ANTHROPIC_API_KEY'] = ATL113_API_KEY;
    $_SERVER['ANTHROPIC_API_KEY'] = ATL113_API_KEY;
    atl113FakeClaude(['loggedIn' => true, 'email' => ATL113_EMAIL, 'authMethod' => 'claude.ai', 'orgName' => 'Owner']);

    $response = $this->getJson('/api/agents/claude-status')->assertOk();
    $body = $response->getContent();

    expect($body)->not->toContain(ATL113_API_KEY)
        ->and($body)->not->toContain(ATL113_EMAIL)
        ->and(preg_match('/[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}/i', (string) $body))->toBe(0)
        ->and($response->json('data.installed'))->toBeTrue()
        ->and($response->json('data.logged_in'))->toBeTrue()
        ->and($response->json('data.api_key_env_clear'))->toBeFalse()
        ->and($response->json('data.ok'))->toBeFalse()
        ->and($response->json('data.login_command'))->toBe('claude login')
        ->and(array_column($response->json('data.checks'), 'name'))->toBe(['claude_cli', 'claude_cli_login', 'claude_api_key'])
        ->and($response->json('data.checks.2.detail'))->toContain('ANTHROPIC_API_KEY');

    // The CLI probes never received the key.
    Process::assertRan(function (PendingProcess $process): bool {
        return ($process->environment['ANTHROPIC_API_KEY'] ?? null) === false;
    });
});

it('reports a missing or logged-out CLI with the login command', function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    atl113FakeClaude(['loggedIn' => false, 'email' => null]);

    $this->getJson('/api/agents/claude-status')->assertOk()
        ->assertJsonPath('data.ok', false)
        ->assertJsonPath('data.installed', true)
        ->assertJsonPath('data.logged_in', false)
        ->assertJsonPath('data.api_key_env_clear', true)
        ->assertJsonPath('data.checks.1.status', 'fail');

    // ATL-122: each distinct check here needs its own, uncached run.
    Cache::forget('agents.claude-status');
    atl113FakeClaude([], versionExit: 127);
    $this->getJson('/api/agents/claude-status')->assertOk()
        ->assertJsonPath('data.installed', false)
        ->assertJsonPath('data.logged_in', false)
        ->assertJsonPath('data.checks.0.status', 'fail');
});

it('reports ok when installed, logged in and no key is set', function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    atl113FakeClaude(['loggedIn' => true]);

    $this->getJson('/api/agents/claude-status')->assertOk()
        ->assertJsonPath('data.ok', true)
        ->assertJsonPath('data.checks.0.detail', 'Installed (2.4.1 (Claude Code))');
});

// ATL-122, security round 11 finding 2: the endpoint used to spawn the claude CLI (and could
// fall back to a paid prompt turn) on every single request, with no throttle or cache.

it('caches the checks for a minute: two calls within that window run the CLI once', function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    $calls = 0;
    Process::fake(function (PendingProcess $process) use (&$calls): FakeProcessResult {
        $calls++;
        $command = is_array($process->command) ? $process->command : [];
        if (in_array('--version', $command, true)) {
            return Process::result(output: "2.4.1 (Claude Code)\n");
        }
        if (in_array('status', $command, true)) {
            return Process::result(output: (string) json_encode(['loggedIn' => true]));
        }

        // The -p probe must never run from this endpoint; fail loudly if it does.
        return Process::result(output: 'unexpected probe call', exitCode: 1);
    });

    $this->getJson('/api/agents/claude-status')->assertOk()->assertJsonPath('data.logged_in', true);
    $this->getJson('/api/agents/claude-status')->assertOk()->assertJsonPath('data.logged_in', true);

    // --version once, auth status once: the second HTTP call was served from cache.
    expect($calls)->toBe(2);
});

it('throttles claude-status to 6 per minute per user; the 7th call gets 429', function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    atl113FakeClaude(['loggedIn' => true]);

    for ($i = 0; $i < 6; $i++) {
        $this->getJson('/api/agents/claude-status')->assertOk();
    }
    $this->getJson('/api/agents/claude-status')->assertTooManyRequests();
});

it('never runs the -p prompt probe from the HTTP status endpoint, even when auth status is unparseable', function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    // No "loggedIn" key: the CLI answered, but not in a shape the endpoint can parse.
    atl113FakeClaude(['unexpected' => 'shape']);

    $this->getJson('/api/agents/claude-status')->assertOk()
        ->assertJsonPath('data.installed', true)
        ->assertJsonPath('data.logged_in', null)
        ->assertJsonPath('data.checks.1.status', 'warn');

    Process::assertNotRan(fn (PendingProcess $p): bool => is_array($p->command) && in_array('-p', $p->command, true));
});
