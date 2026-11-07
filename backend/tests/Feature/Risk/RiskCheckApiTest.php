<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\User;
use App\Support\TradingCore;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\Request as HttpRequest;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\RateLimiter;

/*
 * ATL-128: POST /api/risk/check relays a hand-written proposal to trading_core's signed
 * /internal/risk/check (faked here) and returns its decision, audited.
 */

const ATL128_CORE = 'http://127.0.0.1:8100';
const ATL128_SECRET = 'atl128-test-only-internal-secret';

/**
 * @param  array<string, mixed>  $overrides
 * @return array<string, mixed>
 */
function atl128Proposal(array $overrides = []): array
{
    return array_merge([
        'inst_id' => 'XAU-USDT-SWAP',
        'side' => 'buy',
        'order_type' => 'limit',
        'entry' => '4000',
        'stop' => '3960',
        'take_profits' => [['price' => '4100', 'pct' => '100']],
        'size_suggestion_pct_equity' => '5',
        'leverage_suggestion' => 3,
        'expected_r_multiple' => '2.5',
        'probability_estimate' => '0.5',
        'rationale_md' => 'Hand-written check.',
        'invalidation' => '1H close below 3960.',
    ], $overrides);
}

/**
 * @return array<string, mixed>
 */
function atl128Decision(): array
{
    $checks = [];
    foreach (['env_and_kill_switch', 'instrument_allowed', 'proposal_valid', 'position_size', 'portfolio',
        'loss_limits', 'liquidation_buffer', 'event_blackout', 'autonomy'] as $i => $name) {
        $checks[] = ['n' => $i + 1, 'check' => $name, 'status' => 'passed', 'passed' => true, 'code' => null,
            'detail' => 'OK', 'values' => $name === 'position_size' ? ['binding_cap' => 'max_usdt_per_trade'] : []];
    }

    return [
        'ok' => true,
        'proposal_id' => '0b6f3f8e-2b0e-4c8e-9a51-1d6a7f1c2e3d',
        'risk_check_id' => '5d1c9a2e-7f40-4b9a-8f0e-2a3b4c5d6e7f',
        'env' => 'demo',
        'settings_version' => 7,
        'result' => 'resized',
        'decision' => 'awaiting_owner',
        'reason' => 'Resized by max_usdt_per_trade: 250 contracts, 1000 USD.',
        'failed_check' => null,
        'binding_cap' => 'max_usdt_per_trade',
        'final_contracts' => '250',
        'final_notional_usd' => '1000',
        'leverage' => 3,
        'checks' => $checks,
    ];
}

beforeEach(function (): void {
    config(['services.trading_core.url' => ATL128_CORE, 'services.trading_core.secret' => ATL128_SECRET]);
    $this->user = User::factory()->create();
    RateLimiter::clear('risk-check|'.$this->user->id);
});

it('needs a logged-in owner', function (): void {
    Http::fake();
    $this->postJson('/api/risk/check', atl128Proposal())->assertUnauthorized();
    Http::assertNothingSent();
});

it('relays the proposal signed and returns the same decision, audited', function (): void {
    Http::fake([ATL128_CORE.'/internal/risk/check' => Http::response(atl128Decision())]);

    $this->actingAs($this->user, 'web')
        ->postJson('/api/risk/check', atl128Proposal())
        ->assertOk()
        ->assertExactJson(atl128Decision());

    Http::assertSentCount(1);
    Http::assertSent(function (HttpRequest $request): bool {
        $path = '/internal/risk/check';
        $ts = $request->header(TradingCore::TIMESTAMP_HEADER)[0] ?? '';
        $nonce = $request->header(TradingCore::NONCE_HEADER)[0] ?? '';
        $sig = TradingCore::sign(ATL128_SECRET, $ts, $nonce, 'POST', $path, $request->body());

        return $request->url() === ATL128_CORE.$path
            && $request->method() === 'POST'
            && hash_equals($sig, $request->header(TradingCore::SIGNATURE_HEADER)[0] ?? '')
            && json_decode($request->body(), true) === atl128Proposal();
    });

    $audit = AuditLog::query()->where('action', 'risk.manual_check')->sole();
    expect($audit->user_id)->toBe($this->user->id)
        ->and($audit->subject_type)->toBe('proposals')
        ->and($audit->subject_id)->toBe(atl128Decision()['proposal_id'])
        ->and($audit->after)->toMatchArray([
            'inst_id' => 'XAU-USDT-SWAP', 'side' => 'buy', 'ok' => true, 'result' => 'resized',
            'decision' => 'awaiting_owner', 'risk_check_id' => atl128Decision()['risk_check_id'],
        ]);
});

it('refuses a proposal with no stop with 422 and sends nothing', function (): void {
    Http::fake();
    $body = atl128Proposal();
    unset($body['stop']);

    $this->actingAs($this->user, 'web')
        ->postJson('/api/risk/check', $body)
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['stop']);

    Http::assertNothingSent();
    expect(AuditLog::query()->where('action', 'risk.manual_check')->count())->toBe(0);
});

it('rejects other malformed fields before relaying', function (array $change, string $field): void {
    Http::fake();
    $this->actingAs($this->user, 'web')
        ->postJson('/api/risk/check', atl128Proposal($change))
        ->assertUnprocessable()
        ->assertJsonValidationErrors([$field]);
    Http::assertNothingSent();
})->with([
    'bad side' => [['side' => 'hold'], 'side'],
    'zero stop' => [['stop' => '0'], 'stop'],
    'no take-profit' => [['take_profits' => []], 'take_profits'],
    'probability above 1' => [['probability_estimate' => '1.2'], 'probability_estimate'],
    'bad inst id' => [['inst_id' => '../etc'], 'inst_id'],
]);

it('passes a trading_core 422 through with its errors', function (): void {
    $errors = [['loc' => ['stop'], 'msg' => "Value error, a buy's stop (4050) must be below the entry (4000)", 'type' => 'value_error']];
    Http::fake([ATL128_CORE.'/*' => Http::response(['ok' => false, 'error' => 'invalid', 'errors' => $errors], 422)]);

    $this->actingAs($this->user, 'web')
        ->postJson('/api/risk/check', atl128Proposal(['stop' => '4050']))
        ->assertUnprocessable()
        ->assertJsonPath('error', 'invalid_proposal')
        ->assertJsonPath('errors', $errors);

    expect(AuditLog::query()->where('action', 'risk.manual_check')->sole()->after)
        ->toMatchArray(['ok' => false, 'error' => 'invalid_proposal']);
});

it('maps an engine that cannot run to 409 and an unreachable trading_core to 502', function (): void {
    Http::fake([ATL128_CORE.'/*' => Http::response(['ok' => false, 'error' => 'risk_engine_error', 'detail' => 'no settings row for demo'], 409)]);
    $this->actingAs($this->user, 'web')
        ->postJson('/api/risk/check', atl128Proposal())
        ->assertStatus(409)
        ->assertJsonPath('error', 'risk_engine_unavailable')
        ->assertJsonPath('detail', 'no settings row for demo');

    Http::fake(fn () => throw new ConnectionException('refused'));
    $this->postJson('/api/risk/check', atl128Proposal())
        ->assertStatus(502)
        ->assertJsonPath('error', 'trading_core_unavailable');
});
