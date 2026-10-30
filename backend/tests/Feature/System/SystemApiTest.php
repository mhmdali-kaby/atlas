<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\User;
use App\Support\TradingCore;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\Request as HttpRequest;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Str;

/*
 * ATL-148: /api/system (health, pause, resume, kill), POST /api/orders/{id}/cancel,
 * POST /api/portfolio/positions/{id}/close and GET /api/portfolio/orders. trading_core is faked
 * with Http::fake; its own behaviour (ATL-147) is tested in pytest.
 */

const ATL148_CORE = 'http://127.0.0.1:8100';
const ATL148_SECRET = 'atl148-test-only-internal-secret';

beforeEach(function (): void {
    config([
        'services.trading_core.url' => ATL148_CORE,
        'services.trading_core.secret' => ATL148_SECRET,
        'atlas_defaults.env.default' => 'demo',
    ]);
    $this->user = User::factory()->create();
});

function atl148Signed(HttpRequest $request, string $path): bool
{
    $ts = $request->header(TradingCore::TIMESTAMP_HEADER)[0] ?? '';
    $nonce = $request->header(TradingCore::NONCE_HEADER)[0] ?? '';
    $sig = TradingCore::sign(ATL148_SECRET, $ts, $nonce, $request->method(), $path, $request->body());

    return $request->url() === ATL148_CORE.$path
        && $request->method() === 'POST'
        && hash_equals($sig, $request->header(TradingCore::SIGNATURE_HEADER)[0] ?? '');
}

/** @return array<string, mixed> */
function atl148KillReport(bool $flatten): array
{
    return [
        'ok' => true, 'env' => 'demo', 'flatten' => $flatten, 'at' => '2026-10-31T10:00:00Z', 'paused' => true,
        'cancelled' => 2, 'cancelled_orders' => 1, 'cancelled_algos' => 1, 'already_done' => 0,
        'closed' => $flatten ? 1 : 0, 'stops_kept' => 0, 'failed' => [],
        'items' => [
            ['kind' => 'order', 'inst_id' => 'BTC-USDT-SWAP', 'ref' => '1001', 'action' => 'cancelled', 'attempts' => 1],
            ['kind' => 'position', 'inst_id' => 'BTC-USDT-SWAP', 'ref' => 'k1', 'action' => 'closed', 'attempts' => 1, 'pos_side' => 'net'],
        ],
    ];
}

/** @param array<string, mixed> $overrides */
function atl148Order(string $env, array $overrides = []): string
{
    $id = (string) Str::uuid();
    DB::table('orders')->insert(array_merge([
        'id' => $id,
        'env' => $env,
        'ord_id' => (string) random_int(100000, 999999999),
        'inst_id' => 'BTC-USDT-SWAP',
        'side' => 'buy',
        'pos_side' => 'net',
        'ord_type' => 'limit',
        'td_mode' => 'isolated',
        'px' => '65000.500000000000000000',
        'sz' => '2.000000000000000000',
        'filled_sz' => '0.500000000000000000',
        'state' => 'live',
        'origin' => 'external',
        'purpose' => 'entry',
        'created_at' => Carbon::now(),
        'updated_at' => Carbon::now(),
    ], $overrides));

    return $id;
}

/** @param array<string, mixed> $overrides */
function atl148Algo(string $env, array $overrides = []): string
{
    $id = (string) Str::uuid();
    DB::table('algo_orders')->insert(array_merge([
        'id' => $id,
        'env' => $env,
        'algo_id' => (string) random_int(100000, 999999999),
        'inst_id' => 'BTC-USDT-SWAP',
        'kind' => 'sl',
        'side' => 'sell',
        'pos_side' => 'net',
        'trigger_px' => '64000.000000000000000000',
        'sz' => '1.500000000000000000',
        'state' => 'live',
        'origin' => 'external',
        'created_at' => Carbon::now(),
        'updated_at' => Carbon::now(),
    ], $overrides));

    return $id;
}

function atl148Position(string $env, string $instId = 'XAU-USDT-SWAP', string $posSide = 'long'): string
{
    $id = (string) Str::uuid();
    DB::table('positions_snapshots')->insert([
        'id' => $id, 'env' => $env, 'inst_id' => $instId, 'pos_side' => $posSide, 'pos' => '3',
        'avg_px' => '2400', 'mark_px' => '2410', 'taken_at' => Carbon::now(),
        'created_at' => Carbon::now(), 'updated_at' => Carbon::now(),
    ]);

    return $id;
}

// --- AC1: kill forwards a signed request and returns the report; without confirm 422, nothing sent.

it('forwards a signed kill with flatten and returns trading_core\'s report', function (): void {
    Http::fake([ATL148_CORE.'/internal/system/kill' => Http::response(atl148KillReport(true))]);

    $this->actingAs($this->user, 'web')
        ->postJson('/api/system/kill', ['flatten' => true, 'confirm' => 'KILL'])
        ->assertOk()
        ->assertJsonPath('ok', true)
        ->assertJsonPath('flatten', true)
        ->assertJsonPath('closed', 1)
        ->assertJsonPath('items.1.action', 'closed');

    Http::assertSentCount(1);
    Http::assertSent(fn (HttpRequest $r): bool => atl148Signed($r, '/internal/system/kill')
        && $r->data() === ['flatten' => true]);
});

it('refuses a kill without confirm "KILL" and sends nothing', function (array $body): void {
    Http::fake();

    $this->actingAs($this->user, 'web')
        ->postJson('/api/system/kill', $body)
        ->assertStatus(422)
        ->assertJsonValidationErrors('confirm');

    Http::assertNothingSent();
    expect(AuditLog::query()->count())->toBe(0);
})->with([
    'missing' => [['flatten' => true]],
    'wrong word' => [['flatten' => true, 'confirm' => 'kill']],
    'boolean' => [['confirm' => true]],
]);

it('rejects a non-boolean flatten', function (): void {
    Http::fake();

    $this->actingAs($this->user, 'web')
        ->postJson('/api/system/kill', ['flatten' => 'everything', 'confirm' => 'KILL'])
        ->assertStatus(422)
        ->assertJsonValidationErrors('flatten');
    Http::assertNothingSent();
});

it('sends flatten false when it is omitted', function (): void {
    Http::fake([ATL148_CORE.'/internal/system/kill' => Http::response(atl148KillReport(false))]);

    $this->actingAs($this->user, 'web')
        ->postJson('/api/system/kill', ['confirm' => 'KILL'])
        ->assertOk()
        ->assertJsonPath('flatten', false);
    Http::assertSent(fn (HttpRequest $r): bool => $r->data() === ['flatten' => false]);
});

// --- AC2: the kill switch has its own limiter.

it('still kills after the shared api limiter is exhausted', function (): void {
    config(['atlas_defaults.system_api.api_per_minute' => 2]);
    Http::fake([ATL148_CORE.'/internal/system/*' => Http::response(atl148KillReport(false))]);

    $as = $this->actingAs($this->user, 'web');
    $as->getJson('/api/system/health')->assertOk();
    $as->getJson('/api/portfolio/orders')->assertOk();
    $as->getJson('/api/system/health')->assertStatus(429);
    $as->getJson('/api/portfolio/orders')->assertStatus(429);

    $as->postJson('/api/system/kill', ['confirm' => 'KILL'])->assertOk()->assertJsonPath('ok', true);
    $as->postJson('/api/system/pause', [])->assertOk();
    $as->postJson('/api/system/resume', [])->assertOk();
});

it('limits the kill switch on its own counter', function (): void {
    config(['atlas_defaults.system_api.control_per_minute' => 2]);
    Http::fake([ATL148_CORE.'/internal/system/*' => Http::response(atl148KillReport(false))]);

    $as = $this->actingAs($this->user, 'web');
    $as->postJson('/api/system/kill', ['confirm' => 'KILL'])->assertOk();
    $as->postJson('/api/system/kill', ['confirm' => 'KILL'])->assertOk();
    $as->postJson('/api/system/kill', ['confirm' => 'KILL'])->assertStatus(429);
    // The control routes never touch the shared counter.
    $as->getJson('/api/system/health')->assertOk();
});

// --- AC3: every write writes one audit_log row, including a kill that gets 503.

it('answers 503 when trading_core is down and still audits the kill', function (): void {
    Http::fake(fn () => throw new ConnectionException('Connection refused'));

    $this->actingAs($this->user, 'web')
        ->postJson('/api/system/kill', ['flatten' => true, 'confirm' => 'KILL'])
        ->assertStatus(503)
        ->assertJsonPath('ok', false)
        ->assertJsonPath('error', 'trading_core_unavailable')
        ->assertJsonPath('message', __('system.trading_core_unavailable'));

    $rows = AuditLog::query()->get();
    expect($rows)->toHaveCount(1)
        ->and($rows[0]->action)->toBe('system.killed')
        ->and($rows[0]->user_id)->toBe($this->user->id)
        ->and($rows[0]->before)->toBe(['env' => 'demo', 'flatten' => true])
        ->and($rows[0]->after['http_status'] ?? null)->toBe(503)
        ->and($rows[0]->after['error'] ?? null)->toBe('trading_core_unavailable');
});

it('writes exactly one audit row per write endpoint', function (string $uri, array $body, string $path, string $action, int $status): void {
    $orderId = atl148Order('demo');
    $positionId = atl148Position('demo');
    $uri = str_replace(['{order}', '{position}'], [$orderId, $positionId], $uri);
    $path = str_replace('{order}', $orderId, $path);
    Http::fake([ATL148_CORE.$path => Http::response(['ok' => $status === 200, 'status' => 'done'], $status)]);

    $this->actingAs($this->user, 'web')->postJson($uri, $body)->assertStatus($status);

    Http::assertSent(fn (HttpRequest $r): bool => atl148Signed($r, $path));
    $rows = AuditLog::query()->get();
    expect($rows)->toHaveCount(1)->and($rows[0]->action)->toBe($action);
})->with([
    'pause' => ['/api/system/pause', ['reason' => 'lunch'], '/internal/system/pause', 'system.paused', 200],
    'resume' => ['/api/system/resume', [], '/internal/system/resume', 'system.resumed', 200],
    'kill' => ['/api/system/kill', ['confirm' => 'KILL'], '/internal/system/kill', 'system.killed', 200],
    'cancel' => ['/api/orders/{order}/cancel', [], '/internal/orders/{order}/cancel', 'order.cancel_requested', 200],
    'cancel refused' => ['/api/orders/{order}/cancel', [], '/internal/orders/{order}/cancel', 'order.cancel_requested', 409],
    'close' => ['/api/portfolio/positions/{position}/close', ['confirm' => true], '/internal/positions/XAU-USDT-SWAP/close', 'position.close_requested', 200],
    'close failed' => ['/api/portfolio/positions/{position}/close', ['confirm' => true], '/internal/positions/XAU-USDT-SWAP/close', 'position.close_requested', 502],
]);

it('audits pause, resume, cancel and close when trading_core is down', function (string $uri, array $body, string $action): void {
    $uri = str_replace(['{order}', '{position}'], [atl148Order('demo'), atl148Position('demo')], $uri);
    Http::fake(fn () => throw new ConnectionException('Connection refused'));

    $this->actingAs($this->user, 'web')->postJson($uri, $body)
        ->assertStatus(503)->assertJsonPath('error', 'trading_core_unavailable');

    expect(AuditLog::query()->pluck('action')->all())->toBe([$action]);
})->with([
    ['/api/system/pause', [], 'system.paused'],
    ['/api/system/resume', [], 'system.resumed'],
    ['/api/orders/{order}/cancel', [], 'order.cancel_requested'],
    ['/api/portfolio/positions/{position}/close', ['confirm' => true], 'position.close_requested'],
]);

it('answers 502 for an unexpected trading_core answer', function (): void {
    Http::fake([ATL148_CORE.'/*' => Http::response(['detail' => 'bad signature'], 401)]);

    $this->actingAs($this->user, 'web')->postJson('/api/system/resume')
        ->assertStatus(502)->assertJsonPath('error', 'trading_core_error');
    expect(AuditLog::query()->count())->toBe(1);
});

it('forwards the pause reason and an empty resume body', function (): void {
    Http::fake([ATL148_CORE.'/internal/system/*' => Http::response([
        'ok' => true, 'paused' => true, 'reason' => 'lunch', 'paused_at' => '2026-10-31T10:00:00Z',
        'by' => 'owner', 'active_breakers' => [], 'breakers_cleared' => [],
    ])]);

    $this->actingAs($this->user, 'web')->postJson('/api/system/pause', ['reason' => '  lunch '])
        ->assertOk()->assertJsonPath('paused', true)->assertJsonPath('reason', 'lunch');
    $this->actingAs($this->user, 'web')->postJson('/api/system/resume')->assertOk();

    Http::assertSent(fn (HttpRequest $r): bool => atl148Signed($r, '/internal/system/pause') && $r->data() === ['reason' => 'lunch']);
    Http::assertSent(fn (HttpRequest $r): bool => atl148Signed($r, '/internal/system/resume') && $r->body() === '{}');
});

// --- AC4: GET /api/portfolio/orders.

it('lists open and algo orders of the active env only, sizes as strings', function (): void {
    $open = atl148Order('demo');
    atl148Order('demo', ['state' => 'filled', 'filled_sz' => '2']);
    atl148Order('live');
    $algo = atl148Algo('demo');
    atl148Algo('demo', ['state' => 'canceled']);
    atl148Algo('live');

    $res = $this->actingAs($this->user, 'web')->getJson('/api/portfolio/orders')->assertOk();

    $res->assertJsonPath('data.env', 'demo')
        ->assertJsonCount(1, 'data.orders')
        ->assertJsonCount(1, 'data.algo_orders')
        ->assertJsonPath('data.orders.0.id', $open)
        ->assertJsonPath('data.orders.0.sz', '2')
        ->assertJsonPath('data.orders.0.filled_sz', '0.5')
        ->assertJsonPath('data.orders.0.px', '65000.5')
        ->assertJsonPath('data.orders.0.can_cancel', true)
        ->assertJsonPath('data.algo_orders.0.id', $algo)
        ->assertJsonPath('data.algo_orders.0.sz', '1.5')
        ->assertJsonPath('data.algo_orders.0.trigger_px', '64000')
        ->assertJsonPath('data.algo_orders.0.protects', true);
    expect($res->json('data.orders.0.sz'))->toBeString()
        ->and($res->json('data.algo_orders.0.sz'))->toBeString();

    $this->actingAs($this->user, 'web')->getJson('/api/portfolio/orders?status=algo')
        ->assertJsonCount(0, 'data.orders')->assertJsonCount(1, 'data.algo_orders');
    $this->actingAs($this->user, 'web')->getJson('/api/portfolio/orders?status=open')
        ->assertJsonCount(1, 'data.orders')->assertJsonCount(0, 'data.algo_orders');
    $this->actingAs($this->user, 'web')->getJson('/api/portfolio/orders?status=all')->assertStatus(422);
});

it('follows the active env for the orders list', function (): void {
    config(['atlas_defaults.env.default' => 'live']);
    atl148Order('demo');
    $live = atl148Order('live');

    $this->actingAs($this->user, 'web')->getJson('/api/portfolio/orders?status=open')
        ->assertJsonPath('data.env', 'live')
        ->assertJsonCount(1, 'data.orders')
        ->assertJsonPath('data.orders.0.id', $live);
});

it('needs a signed-in owner', function (string $method, string $uri): void {
    Http::fake();
    $uri = str_replace('{id}', (string) Str::uuid(), $uri);

    $this->json($method, $uri, ['confirm' => 'KILL'])->assertStatus(401);
    Http::assertNothingSent();
    expect(AuditLog::query()->count())->toBe(0);
})->with([
    ['GET', '/api/portfolio/orders'],
    ['GET', '/api/system/health'],
    ['POST', '/api/system/pause'],
    ['POST', '/api/system/resume'],
    ['POST', '/api/system/kill'],
    ['POST', '/api/orders/{id}/cancel'],
    ['POST', '/api/portfolio/positions/{id}/close'],
]);

// --- AC5: other env → 404, nothing sent.

it('answers 404 for an order of the other env and sends nothing', function (): void {
    Http::fake();
    $live = atl148Order('live');

    $this->actingAs($this->user, 'web')->postJson("/api/orders/{$live}/cancel")
        ->assertNotFound()->assertJsonPath('error', 'order_not_found');
    $this->actingAs($this->user, 'web')->postJson('/api/orders/'.Str::uuid().'/cancel')->assertNotFound();
    $this->actingAs($this->user, 'web')->postJson('/api/orders/not-a-uuid/cancel')->assertNotFound();

    Http::assertNothingSent();
    expect(AuditLog::query()->count())->toBe(0);
});

it('answers 404 for a position of the other env and sends nothing', function (): void {
    Http::fake();
    $live = atl148Position('live');

    $this->actingAs($this->user, 'web')->postJson("/api/portfolio/positions/{$live}/close", ['confirm' => true])
        ->assertNotFound()->assertJsonPath('error', 'position_not_found');

    Http::assertNothingSent();
    expect(AuditLog::query()->count())->toBe(0);
});

it('needs confirm to close a position', function (): void {
    Http::fake();
    $id = atl148Position('demo');

    $this->actingAs($this->user, 'web')->postJson("/api/portfolio/positions/{$id}/close", [])
        ->assertStatus(422)->assertJsonValidationErrors('confirm');
    $this->actingAs($this->user, 'web')->postJson("/api/portfolio/positions/{$id}/close", ['confirm' => false])
        ->assertStatus(422);
    Http::assertNothingSent();
});

it('closes the snapshot\'s instrument and side and passes the report through', function (): void {
    $id = atl148Position('demo', 'XAU-USDT-SWAP', 'long');
    Http::fake([ATL148_CORE.'/internal/positions/XAU-USDT-SWAP/close' => Http::response([
        'ok' => true, 'inst_id' => 'XAU-USDT-SWAP', 'status' => 'closed', 'closed' => 1, 'cancelled_algos' => 1, 'items' => [],
    ])]);

    $this->actingAs($this->user, 'web')->postJson("/api/portfolio/positions/{$id}/close", ['confirm' => true])
        ->assertOk()->assertJsonPath('status', 'closed');
    Http::assertSent(fn (HttpRequest $r): bool => atl148Signed($r, '/internal/positions/XAU-USDT-SWAP/close')
        && $r->data() === ['pos_side' => 'long']);
});

it('passes trading_core\'s order_not_open through as 409', function (): void {
    $id = atl148Order('demo');
    Http::fake([ATL148_CORE."/internal/orders/{$id}/cancel" => Http::response([
        'ok' => false, 'error' => 'order_not_open', 'detail' => 'The order is already filled.',
    ], 409)]);

    $this->actingAs($this->user, 'web')->postJson("/api/orders/{$id}/cancel")
        ->assertStatus(409)->assertJsonPath('error', 'order_not_open');
});

// --- health

it('reports pause, active breakers, data freshness, the private socket and reconciliation', function (): void {
    Carbon::setTestNow('2026-10-31T10:00:00Z');
    DB::table('trading_state')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'paused' => true, 'reason' => 'Circuit breaker: okx_errors',
        'paused_at' => '2026-10-31T09:55:00Z',
        'breakers' => json_encode([
            'okx_errors' => ['active' => true, 'since' => '2026-10-31T09:55:00Z', 'reason' => '3 OKX errors', 'self_clearing' => false, 'detail' => ['count' => 3]],
            'data_stale' => ['active' => false, 'since' => '2026-10-31T08:00:00Z', 'reason' => 'old'],
        ]),
        'created_at' => now(), 'updated_at' => now(),
    ]);
    DB::table('sync_status')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'stream' => 'reconcile',
        'last_success_at' => '2026-10-31T09:58:00Z', 'reconciled_at' => '2026-10-31T09:58:00Z', 'last_error' => null,
        'created_at' => now(), 'updated_at' => now(),
    ]);
    atl148Order('demo', ['inst_id' => 'ETH-USDT-SWAP']);
    // Keys of this test only, so parallel checkouts sharing the Redis test DB cannot collide.
    $p = 'atl148:'.Str::lower(Str::random(10));
    config(['atlas_defaults.market_ws.key_prefix' => "{$p}:md", 'atlas_defaults.private_ws.key' => "{$p}:pws"]);
    $redis = Redis::connection('events');
    $nowMs = Carbon::now()->getTimestampMs();
    $keys = ["{$p}:md:demo:BTC-USDT-SWAP", "{$p}:md:demo:ETH-USDT-SWAP", "{$p}:pws:demo"];
    $redis->command('hset', [$keys[0], 'last_msg_ms', (string) ($nowMs - 5000)]);
    $redis->command('hset', [$keys[1], 'last_msg_ms', (string) ($nowMs - 120000)]);
    $redis->command('hset', [$keys[2], 'connected', '1']);
    $redis->command('hset', [$keys[2], 'last_msg_ms', (string) ($nowMs - 2000)]);
    $redis->command('hset', [$keys[2], 'login_ms', (string) ($nowMs - 600000)]);

    try {
        $res = $this->actingAs($this->user, 'web')->getJson('/api/system/health')->assertOk();
    } finally {
        $redis->command('del', $keys);
        Carbon::setTestNow();
    }

    $res->assertJsonPath('data.env', 'demo')
        ->assertJsonPath('data.trading.paused', true)
        ->assertJsonPath('data.trading.reason', 'Circuit breaker: okx_errors')
        ->assertJsonPath('data.trading.paused_at', '2026-10-31T09:55:00Z')
        ->assertJsonPath('data.trading.accepting_new_orders', false)
        ->assertJsonCount(1, 'data.breakers')
        ->assertJsonPath('data.breakers.0.name', 'okx_errors')
        ->assertJsonPath('data.breakers.0.reason', '3 OKX errors')
        ->assertJsonPath('data.market_data.stale', ['ETH-USDT-SWAP'])
        ->assertJsonPath('data.market_data.instruments.0.inst_id', 'BTC-USDT-SWAP')
        ->assertJsonPath('data.market_data.instruments.0.age_seconds', 5)
        ->assertJsonPath('data.market_data.instruments.0.stale', false)
        ->assertJsonPath('data.market_data.instruments.1.stale', true)
        ->assertJsonPath('data.private_ws.connected', true)
        ->assertJsonPath('data.private_ws.stale', false)
        ->assertJsonPath('data.private_ws.age_seconds', 2)
        ->assertJsonPath('data.reconciliation.reconciled_at', '2026-10-31T09:58:00Z')
        ->assertJsonPath('data.reconciliation.last_error', null);
});

it('reports a fresh install as not paused with a stale private socket', function (): void {
    config(['atlas_defaults.private_ws.key' => 'atl148:'.Str::lower(Str::random(10)).':pws']);

    $this->actingAs($this->user, 'web')->getJson('/api/system/health')->assertOk()
        ->assertJsonPath('data.trading.paused', false)
        ->assertJsonPath('data.trading.accepting_new_orders', true)
        ->assertJsonPath('data.breakers', [])
        ->assertJsonPath('data.private_ws.connected', false)
        ->assertJsonPath('data.private_ws.stale', true)
        ->assertJsonPath('data.reconciliation.reconciled_at', null);
});
