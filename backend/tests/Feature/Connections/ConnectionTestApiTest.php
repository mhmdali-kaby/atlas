<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Connection;
use App\Models\User;
use App\Support\TradingCore;
use Illuminate\Http\Client\Request;
use Illuminate\Log\Events\MessageLogged;
use Illuminate\Support\Facades\Event;
use Illuminate\Support\Facades\Http;

const ATL8_CORE = 'http://127.0.0.1:8100';
const ATL8_SECRET = 'atl8-test-only-internal-secret';

/**
 * @param  array<string, mixed>  $overrides
 * @return array<string, mixed>
 */
function coreResult(array $overrides = []): array
{
    return [
        'ok' => true,
        'acct_lv' => '2',
        'account_mode' => 'futures',
        'position_mode' => 'net_mode',
        'permissions' => ['read_only', 'trade'],
        'uid_last4' => '6123',
        'error' => null,
        'okx_code' => null,
        'verified_at' => '2026-10-28T08:00:00.000000Z',
        ...$overrides,
    ];
}

beforeEach(function (): void {
    config(['services.trading_core.url' => ATL8_CORE, 'services.trading_core.secret' => ATL8_SECRET]);
    $this->actingAs(User::factory()->create(), 'web');
    $this->connection = Connection::query()->create([
        'exchange' => 'okx',
        'env' => 'demo',
        'label' => 'OKX Demo',
        'api_key' => 'atl8-key-abcd',
        'api_secret' => 'atl8-secret',
        'passphrase' => 'atl8-passphrase',
    ]);
    $this->url = "/api/connections/{$this->connection->id}/test";
});

it('requires authentication', function (): void {
    Http::fake();
    auth()->guard('web')->logout();

    $this->postJson($this->url)->assertUnauthorized();
    Http::assertNothingSent();
});

it('relays a verified result from a signed trading_core call and audits it', function (): void {
    Http::fake(function (Request $request) {
        // trading_core writes the row itself; emulate that so the relayed resource is fresh.
        Connection::query()->whereKey($this->connection->id)->update([
            'account_mode' => 'futures', 'position_mode' => 'net_mode', 'permissions' => json_encode(['read_only', 'trade']),
            'uid_last4' => '6123', 'last_verified_at' => now(),
        ]);

        return Http::response(coreResult());
    });

    $this->postJson($this->url)
        ->assertOk()
        ->assertJsonPath('ok', true)
        ->assertJsonPath('acct_lv', '2')
        ->assertJsonPath('account_mode', 'futures')
        ->assertJsonPath('position_mode', 'net_mode')
        ->assertJsonPath('permissions', ['read_only', 'trade'])
        ->assertJsonPath('uid_last4', '6123')
        ->assertJsonPath('error', null)
        ->assertJsonPath('message', null)
        ->assertJsonPath('connection.uid_last4', '6123')
        ->assertJsonPath('connection.account_mode', 'futures');

    Http::assertSent(function (Request $request): bool {
        $path = "/internal/connections/{$this->connection->id}/test";
        $ts = $request->header(TradingCore::TIMESTAMP_HEADER)[0];

        return $request->method() === 'POST'
            && $request->url() === ATL8_CORE.$path
            && $request->header(TradingCore::SIGNATURE_HEADER)[0]
                === TradingCore::sign(ATL8_SECRET, $ts, 'POST', $path, $request->body());
    });

    $audit = AuditLog::query()->where('action', 'connection.tested')->sole();
    expect($audit->subject_id)->toBe($this->connection->id)
        ->and($audit->after)->toMatchArray(['ok' => true, 'uid_last4' => '6123', 'acct_lv' => '2'])
        ->and(json_encode($audit->after))->not->toContain('atl8-secret');
});

it('reports a withdraw-enabled live key as refused', function (): void {
    $this->connection->update(['env' => 'live', 'label' => 'OKX Live']);
    Http::fake(['*' => Http::response(coreResult([
        'ok' => false, 'permissions' => ['read_only', 'trade', 'withdraw'], 'warnings' => [],
        'error' => 'withdraw_permission_enabled', 'verified_at' => null,
    ]))]);

    $this->postJson($this->url)
        ->assertOk()
        ->assertJsonPath('ok', false)
        ->assertJsonPath('error', 'withdraw_permission_enabled')
        ->assertJsonPath('warnings', [])
        ->assertJsonPath('message', 'This key can withdraw funds. Create a key with Read + Trade only.')
        ->assertJsonPath('connection.last_verified_at', null);
});

it('relays a withdraw-enabled demo key as verified with a warning (ATL-30)', function (): void {
    Http::fake(function () {
        Connection::query()->whereKey($this->connection->id)->update([
            'permissions' => json_encode(['read_only', 'trade', 'withdraw']), 'last_verified_at' => now(),
        ]);

        return Http::response(coreResult([
            'permissions' => ['read_only', 'trade', 'withdraw'], 'warnings' => ['withdraw_permission_enabled'],
        ]));
    });

    $this->postJson($this->url)
        ->assertOk()
        ->assertJsonPath('ok', true)
        ->assertJsonPath('error', null)
        ->assertJsonPath('permissions', ['read_only', 'trade', 'withdraw'])
        ->assertJsonPath('warnings', ['withdraw_permission_enabled'])
        ->assertJsonPath('connection.last_verified_at', fn ($v): bool => is_string($v) && $v !== '');

    expect(AuditLog::query()->where('action', 'connection.tested')->sole()->after)
        ->toMatchArray(['ok' => true, 'warnings' => ['withdraw_permission_enabled']]);
});

it('relays an empty warnings list when trading_core sends none', function (): void {
    Http::fake(['*' => Http::response(coreResult())]);

    $this->postJson($this->url)->assertOk()->assertJsonPath('warnings', []);
});

it('maps OKX credential and environment errors to short messages', function (string $slug, string $code, string $message): void {
    Http::fake(['*' => Http::response([
        'ok' => false, 'acct_lv' => null, 'account_mode' => null, 'position_mode' => null, 'permissions' => [],
        'uid_last4' => null, 'error' => $slug, 'okx_code' => $code, 'verified_at' => null,
    ])]);

    $this->postJson($this->url)
        ->assertOk()
        ->assertJsonPath('ok', false)
        ->assertJsonPath('okx_code', $code)
        ->assertJsonPath('message', $message);
})->with([
    'wrong sign' => ['invalid_credentials', '50113', 'Key or passphrase is wrong'],
    'wrong key' => ['invalid_credentials', '50111', 'Key or passphrase is wrong'],
    'other env' => ['wrong_environment', '50101', 'This key belongs to the other environment (demo/live)'],
    'unmapped' => ['some_new_slug', '59999', 'OKX refused the request'],
]);

it('returns 503 trading_core_unavailable within 5 seconds when trading_core is stopped', function (): void {
    // Nothing listens on port 1: a real connection refusal, no fake.
    config(['services.trading_core.url' => 'http://127.0.0.1:1']);
    $logged = [];
    Event::listen(MessageLogged::class, function (MessageLogged $e) use (&$logged): void {
        $logged[] = $e->message.json_encode($e->context);
    });

    $started = microtime(true);
    $this->postJson($this->url)
        ->assertStatus(503)
        ->assertExactJson([
            'ok' => false,
            'error' => 'trading_core_unavailable',
            'message' => 'The trading service is not running. Start Atlas and try again.',
        ]);

    expect(microtime(true) - $started)->toBeLessThan(5.0)
        ->and(AuditLog::query()->where('action', 'connection.tested')->count())->toBe(0)
        ->and(implode("\n", $logged))->not->toContain('atl8-secret');
});

it('returns 503 when the shared secret is not configured', function (): void {
    Http::fake();
    config(['services.trading_core.secret' => '']);

    $this->postJson($this->url)->assertStatus(503)->assertJsonPath('error', 'trading_core_unavailable');
    Http::assertNothingSent();
});

it('returns 502 when trading_core answers with an error', function (): void {
    Http::fake(['*' => Http::response(['detail' => 'invalid or missing signature'], 401)]);

    $this->postJson($this->url)->assertStatus(502)->assertJsonPath('error', 'trading_core_error');
});

it('returns 404 for an unknown or non-uuid connection', function (): void {
    Http::fake();

    $this->postJson('/api/connections/'.fake()->uuid().'/test')->assertNotFound();
    $this->postJson('/api/connections/not-a-uuid/test')->assertNotFound();
    Http::assertNothingSent();
});
