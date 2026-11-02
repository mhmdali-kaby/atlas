<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Connection;
use App\Models\Setting;
use App\Models\SystemState;
use App\Models\User;
use App\Support\Events\EventBus;
use App\Support\Settings\SettingsService;
use App\Support\System\ActiveEnv;
use Carbon\CarbonImmutable;
use Illuminate\Database\QueryException;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Str;

/*
 * ATL-236, decision #255 (4)(5): the stored active env, the live pre-flight and the guarded switch.
 */

const ATL236_PASSWORD = 'atl236-owner-password';

beforeEach(function (): void {
    config(['atlas_defaults.env.default' => 'demo']);
    $this->owner = User::factory()->create(['password' => Hash::make(ATL236_PASSWORD)]);
    $this->actingAs($this->owner, 'web');
    RateLimiter::clear('environment-switch|'.$this->owner->id);
});

/** @return list<array<string, mixed>> every envelope of $type on atlas.system */
function atl236Events(string $type): array
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
        if (is_array($envelope) && $envelope['type'] === $type) {
            $found[] = $envelope;
        }
    }

    return $found;
}

/** Makes every computed pre-flight item true and ticks the three attestations. */
function atl236SatisfyPreflight(): void
{
    $connection = Connection::query()->create([
        'exchange' => 'okx', 'env' => 'live', 'label' => 'OKX Live',
        'api_key' => 'atl236-live-key-wxyz', 'api_secret' => 'atl236-secret', 'passphrase' => 'atl236-pass',
        'permissions' => ['read_only', 'trade'], 'last_verified_at' => CarbonImmutable::now(),
    ]);
    $connection->forceFill(['withdraw_off_verified_at' => CarbonImmutable::now()])->save();

    $at = CarbonImmutable::now()->subDays(30);
    DB::table('equity_curve')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'ts' => $at, 'total_eq_usd' => '5000000',
        'created_at' => $at, 'updated_at' => $at,
    ]);

    $live = app(SettingsService::class)->current('live');
    $live->data = [...$live->data, 'autonomy_level' => 2, 'bot_capital_cap_usdt' => '10000'];
    $live->autonomy_level = 2;
    $live->save();

    test()->putJson('/api/environment/attestations', ['ip_allowlist' => true, 'sub_account' => true, 'demo_reviewed' => true])
        ->assertOk();
}

/** @return array<string, array<string, mixed>> */
function atl236Items(array $data): array
{
    $items = [];
    foreach ($data['preflight']['items'] as $item) {
        $items[$item['key']] = $item;
    }

    return $items;
}

it('requires authentication', function (): void {
    auth()->guard('web')->logout();

    $this->getJson('/api/environment')->assertUnauthorized();
    $this->postJson('/api/environment/switch', ['target' => 'demo', 'confirm' => true])->assertUnauthorized();
    $this->putJson('/api/environment/attestations', ['ip_allowlist' => true])->assertUnauthorized();
});

it('reports demo and eight pre-flight items on a fresh database', function (): void {
    $data = $this->getJson('/api/environment')->assertOk()->json('data');

    expect($data['active_env'])->toBe('demo')
        ->and($data['live_started_at'])->toBeNull()
        ->and($data['first_month_ends_at'])->toBeNull()
        ->and($data['preflight']['ok'])->toBeFalse()
        ->and($data['preflight']['items'])->toHaveCount(8);

    foreach ($data['preflight']['items'] as $item) {
        expect($item)->toHaveKeys(['key', 'kind', 'ok', 'detail'])
            ->and($item['ok'])->toBeBool()
            ->and($item['detail'])->toBeString()->not->toBe('');
    }

    $items = atl236Items($data);
    expect(array_keys($items))->toBe([
        'live_connection', 'demo_history', 'live_risk_profile', 'live_autonomy', 'live_capital_cap',
        'ip_allowlist', 'sub_account', 'demo_reviewed',
    ])
        ->and($items['live_connection']['ok'])->toBeFalse()
        ->and($items['live_connection']['detail'])->toContain('No live OKX connection');

    // Reading never creates the live settings row or the system_state row.
    expect(Setting::query()->where('env', 'live')->exists())->toBeFalse()
        ->and(SystemState::query()->exists())->toBeFalse();
});

it('fails the connection item for an unverified or withdraw-unconfirmed live key', function (): void {
    $connection = Connection::query()->create([
        'exchange' => 'okx', 'env' => 'live', 'label' => 'OKX Live',
        'api_key' => 'k-abcd', 'api_secret' => 's', 'passphrase' => 'p',
    ]);
    expect(atl236Items($this->getJson('/api/environment')->json('data'))['live_connection']['ok'])->toBeFalse();

    $connection->forceFill(['last_verified_at' => CarbonImmutable::now()])->save();
    $item = atl236Items($this->getJson('/api/environment')->json('data'))['live_connection'];
    expect($item['ok'])->toBeFalse()->and($item['detail'])->toContain('cannot withdraw');

    $connection->forceFill(['withdraw_off_verified_at' => CarbonImmutable::now()])->save();
    expect(atl236Items($this->getJson('/api/environment')->json('data'))['live_connection']['ok'])->toBeTrue();
});

it('needs 28 days of demo equity history', function (): void {
    $at = CarbonImmutable::now()->subDays(27);
    DB::table('equity_curve')->insert(['id' => (string) Str::uuid(), 'env' => 'demo', 'ts' => $at, 'total_eq_usd' => '1', 'created_at' => $at, 'updated_at' => $at]);
    $item = atl236Items($this->getJson('/api/environment')->json('data'))['demo_history'];
    expect($item['ok'])->toBeFalse()->and($item['detail'])->toBe('27 of 28 days of demo history.');

    $at = CarbonImmutable::now()->subDays(28)->subMinute();
    DB::table('equity_curve')->insert(['id' => (string) Str::uuid(), 'env' => 'demo', 'ts' => $at, 'total_eq_usd' => '1', 'created_at' => $at, 'updated_at' => $at]);
    expect(atl236Items($this->getJson('/api/environment')->json('data'))['demo_history']['ok'])->toBeTrue();
});

it('fails live autonomy above 2 and a missing live capital cap', function (): void {
    $live = app(SettingsService::class)->current('live');
    $live->data = [...$live->data, 'autonomy_level' => 3, 'bot_capital_cap_usdt' => null];
    $live->save();

    $items = atl236Items($this->getJson('/api/environment')->json('data'));
    expect($items['live_risk_profile']['ok'])->toBeTrue()
        ->and($items['live_autonomy']['ok'])->toBeFalse()
        ->and($items['live_capital_cap']['ok'])->toBeFalse();
});

it('sets and clears attestations, audited', function (): void {
    $data = $this->putJson('/api/environment/attestations', ['ip_allowlist' => true, 'sub_account' => true])
        ->assertOk()->json('data');
    $items = atl236Items($data);
    expect($items['ip_allowlist']['ok'])->toBeTrue()
        ->and($items['sub_account']['ok'])->toBeTrue()
        ->and($items['demo_reviewed']['ok'])->toBeFalse()
        ->and(AuditLog::query()->where('action', 'environment.attestations_updated')->count())->toBe(1);

    $items = atl236Items($this->putJson('/api/environment/attestations', ['ip_allowlist' => false])->assertOk()->json('data'));
    expect($items['ip_allowlist']['ok'])->toBeFalse()
        ->and($items['sub_account']['ok'])->toBeTrue()
        ->and(AuditLog::query()->where('action', 'environment.attestations_updated')->count())->toBe(2);

    // Nothing changed: no audit row.
    $this->putJson('/api/environment/attestations', ['sub_account' => true])->assertOk();
    expect(AuditLog::query()->where('action', 'environment.attestations_updated')->count())->toBe(2);

    $this->putJson('/api/environment/attestations', ['sub_account' => 'yes'])->assertUnprocessable()->assertJsonValidationErrors('sub_account');
});

it('switches to live when every item passes, pauses live, audits and publishes', function (): void {
    atl236SatisfyPreflight();
    expect($this->getJson('/api/environment')->json('data.preflight.ok'))->toBeTrue();
    $before = count(atl236Events('system.env_changed'));

    $data = $this->postJson('/api/environment/switch', [
        'target' => 'live', 'password' => ATL236_PASSWORD, 'confirmation' => 'TRADE LIVE',
    ])->assertOk()->json('data');

    expect($data['active_env'])->toBe('live')
        ->and($data['live_started_at'])->not->toBeNull()
        ->and($data['first_month_ends_at'])->not->toBeNull()
        ->and($data['in_first_live_month'])->toBeTrue()
        ->and(ActiveEnv::get())->toBe('live');

    $state = SystemState::query()->findOrFail(1);
    expect($state->active_env)->toBe('live')->and($state->live_started_at)->not->toBeNull();

    $trading = DB::table('trading_state')->where('env', 'live')->first();
    expect($trading)->not->toBeNull()->and((bool) $trading->paused)->toBeTrue();

    expect(AuditLog::query()->where('action', 'environment.switched')->count())->toBe(1);
    $audit = AuditLog::query()->where('action', 'environment.switched')->firstOrFail();
    expect($audit->before)->toBe(['active_env' => 'demo'])
        ->and($audit->after['active_env'])->toBe('live');

    $events = atl236Events('system.env_changed');
    expect(count($events))->toBe($before + 1);
    $last = end($events);
    expect($last['payload']['from'])->toBe('demo')
        ->and($last['payload']['to'])->toBe('live')
        ->and($last['env'])->toBe('live');
});

it('refuses a wrong password alone, without the confirmation or pre-flight state', function (): void {
    $response = $this->postJson('/api/environment/switch', [
        'target' => 'live', 'password' => 'wrong-password', 'confirmation' => 'trade live',
    ])->assertUnprocessable();

    expect(array_keys($response->json('errors')))->toBe(['password'])
        ->and($response->json('failing_items'))->toBe([])
        ->and(ActiveEnv::get())->toBe('demo')
        ->and(AuditLog::query()->where('action', 'environment.switched')->count())->toBe(0);
});

it('refuses a confirmation that is not exactly TRADE LIVE', function (): void {
    atl236SatisfyPreflight();

    $this->postJson('/api/environment/switch', [
        'target' => 'live', 'password' => ATL236_PASSWORD, 'confirmation' => 'trade live',
    ])->assertUnprocessable()->assertJsonValidationErrors('confirmation')->assertJsonMissingValidationErrors('preflight');

    expect(ActiveEnv::get())->toBe('demo');
});

it('refuses when any pre-flight item fails and names it', function (): void {
    atl236SatisfyPreflight();
    $this->putJson('/api/environment/attestations', ['demo_reviewed' => false])->assertOk();

    $response = $this->postJson('/api/environment/switch', [
        'target' => 'live', 'password' => ATL236_PASSWORD, 'confirmation' => 'TRADE LIVE',
    ])->assertUnprocessable()->assertJsonValidationErrors('preflight');

    expect($response->json('failing_items'))->toBe(['demo_reviewed'])
        ->and($response->json('errors.preflight.0'))->toContain('Demo results reviewed')
        ->and(ActiveEnv::get())->toBe('demo')
        ->and(DB::table('trading_state')->where('env', 'live')->exists())->toBeFalse();
});

it('refuses a live switch with the live connection missing', function (): void {
    $response = $this->postJson('/api/environment/switch', [
        'target' => 'live', 'password' => ATL236_PASSWORD, 'confirmation' => 'TRADE LIVE',
    ])->assertUnprocessable();

    expect($response->json('failing_items'))->toContain('live_connection')
        ->and(ActiveEnv::get())->toBe('demo');
});

it('requires password and confirmation for live and confirm for demo', function (): void {
    $this->postJson('/api/environment/switch', ['target' => 'live'])
        ->assertUnprocessable()->assertJsonValidationErrors(['password', 'confirmation']);
    $this->postJson('/api/environment/switch', ['target' => 'paper'])
        ->assertUnprocessable()->assertJsonValidationErrors('target');
});

it('limits switch attempts to 5 a minute', function (): void {
    for ($i = 0; $i < 5; $i++) {
        $this->postJson('/api/environment/switch', [
            'target' => 'live', 'password' => 'guess-'.$i, 'confirmation' => 'TRADE LIVE',
        ])->assertUnprocessable();
    }

    $this->postJson('/api/environment/switch', [
        'target' => 'live', 'password' => ATL236_PASSWORD, 'confirmation' => 'TRADE LIVE',
    ])->assertTooManyRequests();
    expect(ActiveEnv::get())->toBe('demo');
});

it('switches back to demo with confirm only, and ActiveEnv follows both ways', function (): void {
    atl236SatisfyPreflight();
    $this->postJson('/api/environment/switch', [
        'target' => 'live', 'password' => ATL236_PASSWORD, 'confirmation' => 'TRADE LIVE',
    ])->assertOk();
    expect(ActiveEnv::get())->toBe('live');
    $started = SystemState::query()->findOrFail(1)->live_started_at;

    // No password, no pre-flight.
    $this->putJson('/api/environment/attestations', ['demo_reviewed' => false])->assertOk();
    $this->postJson('/api/environment/switch', ['target' => 'demo'])
        ->assertUnprocessable()->assertJsonValidationErrors('confirm');
    $data = $this->postJson('/api/environment/switch', ['target' => 'demo', 'confirm' => true])->assertOk()->json('data');

    expect($data['active_env'])->toBe('demo')
        ->and(ActiveEnv::get())->toBe('demo')
        ->and((bool) DB::table('trading_state')->where('env', 'demo')->value('paused'))->toBeTrue()
        ->and(SystemState::query()->findOrFail(1)->live_started_at?->equalTo($started))->toBeTrue()
        ->and(AuditLog::query()->where('action', 'environment.switched')->count())->toBe(2);

    $last = collect(atl236Events('system.env_changed'))->last();
    expect($last['payload'])->toMatchArray(['from' => 'live', 'to' => 'demo']);
});

it('answers 409 for a switch to the env already active', function (): void {
    $this->postJson('/api/environment/switch', ['target' => 'demo', 'confirm' => true])
        ->assertStatus(409)->assertJson(['error' => 'already_active']);
    expect(AuditLog::query()->where('action', 'environment.switched')->count())->toBe(0);
});

it('falls back to the configured env only while the row is missing', function (): void {
    config(['atlas_defaults.env.default' => 'live']);
    expect(ActiveEnv::get())->toBe('live');

    DB::table('system_state')->insert(['id' => 1, 'active_env' => 'demo', 'preflight_attestations' => '{}', 'created_at' => now(), 'updated_at' => now()]);
    expect(ActiveEnv::get())->toBe('demo');
});

it('keeps one system_state row only', function (): void {
    DB::table('system_state')->insert(['id' => 1, 'active_env' => 'demo', 'preflight_attestations' => '{}', 'created_at' => now(), 'updated_at' => now()]);

    expect(fn () => DB::table('system_state')->insert(['id' => 2, 'active_env' => 'live', 'preflight_attestations' => '{}', 'created_at' => now(), 'updated_at' => now()]))
        ->toThrow(QueryException::class);
});

it('answers in Arabic when asked', function (): void {
    $this->withHeader('Accept-Language', 'ar')
        ->postJson('/api/environment/switch', ['target' => 'live', 'password' => 'nope', 'confirmation' => 'TRADE LIVE'])
        ->assertUnprocessable()
        ->assertJsonPath('errors.password.0', 'كلمة المرور غير صحيحة.');
});
