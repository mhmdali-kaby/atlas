<?php

declare(strict_types=1);

use App\Jobs\RunBacktest;
use App\Models\AuditLog;
use App\Models\BacktestRun;
use App\Models\User;
use App\Support\TradingCore;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\Request as HttpRequest;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Queue;
use Illuminate\Support\Facades\RateLimiter;

/*
 * ATL-208: /api/backtests stores an owner backtest, a queued job calls trading_core's signed
 * POST /internal/backtest (faked here) and stores the result or trading_core's reason.
 */

const ATL208_CORE = 'http://127.0.0.1:8100';
const ATL208_SECRET = 'atl208-test-only-internal-secret';

/**
 * @param  array<string, mixed>  $overrides
 * @return array<string, mixed>
 */
function atl208Body(array $overrides = []): array
{
    return array_replace([
        'rule_spec' => [
            'name' => 'sma_10_30_cross',
            'side' => 'long',
            'indicators' => ['sma10' => ['kind' => 'sma', 'window' => 10], 'sma30' => ['kind' => 'sma', 'window' => 30]],
            'entry' => [['left' => 'sma10', 'op' => 'crosses_above', 'right' => 'sma30']],
            'exit' => [['left' => 'sma10', 'op' => 'crosses_below', 'right' => 'sma30']],
            'stop_pct' => 3,
            'take_profit_pct' => null,
        ],
        'inst_ids' => ['BTC-USDT-SWAP', 'XAU-USDT-SWAP'],
        'bar' => '1H',
        'period' => ['start' => '2026-05-01T00:00:00Z', 'end' => '2026-10-01T00:00:00Z'],
        'splits' => 3,
        'slippage_bps' => 2,
    ], $overrides);
}

/**
 * @return array<string, mixed>
 */
function atl208Result(): array
{
    return [
        'ok' => true,
        'env' => 'demo',
        'rule' => atl208Body()['rule_spec'],
        'bar' => '1H',
        'period' => ['start' => '2026-05-01T00:00:00+00:00', 'end' => '2026-10-01T00:00:00+00:00'],
        'splits' => 3,
        'slippage_bps' => 2.0,
        'n_trades' => 18,
        'data_gap' => false,
        'overfit_warning' => false,
        'gap_notes' => [],
        'verdict_cap' => null,
        'instruments' => [['inst_id' => 'BTC-USDT-SWAP', 'full' => ['n_trades' => 18, 'sharpe' => 0.4]]],
        'excluded' => [['inst_id' => 'XAU-USDT-SWAP', 'reason' => 'no 1H candles in demo for the period']],
        'data_as_of' => '2026-10-01T00:00:00+00:00',
    ];
}

function atl208Run(User $user, string $env = 'demo', string $status = 'queued'): BacktestRun
{
    return BacktestRun::query()->create([
        'env' => $env,
        'user_id' => $user->id,
        'request' => atl208Body(),
        'status' => $status,
    ]);
}

beforeEach(function (): void {
    config(['services.trading_core.url' => ATL208_CORE, 'services.trading_core.secret' => ATL208_SECRET]);
    $this->user = User::factory()->create();
    RateLimiter::clear('backtests-start|'.$this->user->id);
});

it('needs a logged-in owner on every endpoint', function (): void {
    Queue::fake();
    $run = atl208Run(User::factory()->create());

    $this->postJson('/api/backtests', atl208Body())->assertUnauthorized();
    $this->getJson('/api/backtests')->assertUnauthorized();
    $this->getJson('/api/backtests/'.$run->id)->assertUnauthorized();

    Queue::assertNothingPushed();
    expect(BacktestRun::query()->count())->toBe(1);
});

it('accepts a valid backtest with 202, a queued row, an audit entry and a job on the backtests queue', function (): void {
    Queue::fake();

    $response = $this->actingAs($this->user, 'web')
        ->postJson('/api/backtests', atl208Body())
        ->assertStatus(202)
        ->assertJsonPath('ok', true)
        ->assertJsonPath('status', 'queued');

    $run = BacktestRun::query()->sole();
    expect($response->json('id'))->toBe($run->id)
        ->and($run->env)->toBe('demo')
        ->and($run->user_id)->toBe($this->user->id)
        ->and($run->status)->toBe('queued')
        ->and($run->request)->toEqual(atl208Body())
        ->and($run->result)->toBeNull();

    $audit = AuditLog::query()->where('action', 'backtest.started')->sole();
    expect($audit->user_id)->toBe($this->user->id)
        ->and($audit->subject_type)->toBe('backtest_runs')
        ->and($audit->subject_id)->toBe($run->id)
        ->and($audit->after)->toMatchArray([
            'env' => 'demo', 'rule' => 'sma_10_30_cross', 'inst_ids' => ['BTC-USDT-SWAP', 'XAU-USDT-SWAP'], 'bar' => '1H',
        ]);

    Queue::assertPushedOn('backtests', RunBacktest::class, fn (RunBacktest $job): bool => $job->runId === $run->id);
});

it('drops optional nulls and an empty indicator map before storing the request', function (): void {
    Queue::fake();
    $body = atl208Body(['splits' => null, 'slippage_bps' => null]);
    $body['rule_spec']['indicators'] = [];
    $body['rule_spec']['entry'] = [['left' => 'close', 'op' => 'above', 'right' => 100.5]];
    $body['rule_spec']['exit'] = [];

    $this->actingAs($this->user, 'web')->postJson('/api/backtests', $body)->assertStatus(202);

    $request = BacktestRun::query()->sole()->request;
    expect($request)->not->toHaveKeys(['splits', 'slippage_bps'])
        ->and($request['rule_spec'])->not->toHaveKey('indicators')
        ->and($request['rule_spec']['entry'][0]['right'])->toBe(100.5);
});

it('refuses an invalid rule spec with 422 and queues nothing', function (Closure $change, string $field): void {
    Queue::fake();
    $body = atl208Body();
    $change($body);

    $this->actingAs($this->user, 'web')
        ->postJson('/api/backtests', $body)
        ->assertUnprocessable()
        ->assertJsonValidationErrors([$field]);

    Queue::assertNothingPushed();
    expect(BacktestRun::query()->count())->toBe(0)
        ->and(AuditLog::query()->where('action', 'backtest.started')->count())->toBe(0);
})->with([
    'unknown indicator' => [function (array &$b): void {
        $b['rule_spec']['entry'][0]['right'] = 'sma99';
    }, 'rule_spec.entry.0.right'],
    'no entry' => [function (array &$b): void {
        $b['rule_spec']['entry'] = [];
    }, 'rule_spec.entry'],
    'bad op' => [function (array &$b): void {
        $b['rule_spec']['exit'][0]['op'] = 'equals';
    }, 'rule_spec.exit.0.op'],
    'bad kind' => [function (array &$b): void {
        $b['rule_spec']['indicators']['sma10']['kind'] = 'macd';
    }, 'rule_spec.indicators.sma10.kind'],
    'zero window' => [function (array &$b): void {
        $b['rule_spec']['indicators']['sma10']['window'] = 0;
    }, 'rule_spec.indicators.sma10.window'],
    'close as indicator' => [function (array &$b): void {
        $b['rule_spec']['indicators']['close'] = ['kind' => 'sma', 'window' => 5];
    }, 'rule_spec.indicators'],
    'stop 100%' => [function (array &$b): void {
        $b['rule_spec']['stop_pct'] = 100;
    }, 'rule_spec.stop_pct'],
    'unknown rule key' => [function (array &$b): void {
        $b['rule_spec']['leverage'] = 5;
    }, 'rule_spec'],
    'unknown top-level key' => [function (array &$b): void {
        $b['execute'] = true;
    }, 'execute'],
    'bad side' => [function (array &$b): void {
        $b['rule_spec']['side'] = 'both';
    }, 'rule_spec.side'],
    'bad bar' => [function (array &$b): void {
        $b['bar'] = '2H';
    }, 'bar'],
    'too many instruments' => [function (array &$b): void {
        $b['inst_ids'] = ['A-USDT-SWAP', 'B-USDT-SWAP', 'C-USDT-SWAP', 'D-USDT-SWAP', 'E-USDT-SWAP', 'F-USDT-SWAP'];
    }, 'inst_ids'],
    'repeated instrument' => [function (array &$b): void {
        $b['inst_ids'] = ['BTC-USDT-SWAP', 'BTC-USDT-SWAP'];
    }, 'inst_ids.0'],
    'naive datetime' => [function (array &$b): void {
        $b['period']['start'] = '2026-05-01T00:00:00';
    }, 'period.start'],
    'period reversed' => [function (array &$b): void {
        $b['period'] = ['start' => '2026-10-01T00:00:00Z', 'end' => '2026-05-01T00:00:00Z'];
    }, 'period.end'],
    'splits 11' => [function (array &$b): void {
        $b['splits'] = 11;
    }, 'splits'],
    'slippage 1001' => [function (array &$b): void {
        $b['slippage_bps'] = 1001;
    }, 'slippage_bps'],
]);

it('throttles the 7th start in a minute with 429', function (): void {
    Queue::fake();

    for ($i = 0; $i < 6; $i++) {
        $this->actingAs($this->user, 'web')->postJson('/api/backtests', atl208Body())->assertStatus(202);
    }
    $this->actingAs($this->user, 'web')->postJson('/api/backtests', atl208Body())->assertStatus(429);

    expect(BacktestRun::query()->count())->toBe(6);
    Queue::assertPushed(RunBacktest::class, 6);
});

it('runs the job signed and stores status done with the result', function (): void {
    Http::fake([ATL208_CORE.'/internal/backtest' => Http::response(atl208Result())]);
    $run = atl208Run($this->user);

    (new RunBacktest($run->id))->handle();

    $run->refresh();
    expect($run->status)->toBe('done')
        ->and($run->result)->toEqual(atl208Result())
        ->and($run->error)->toBeNull()
        ->and($run->started_at)->not->toBeNull()
        ->and($run->finished_at)->not->toBeNull();

    Http::assertSentCount(1);
    Http::assertSent(function (HttpRequest $request): bool {
        $path = '/internal/backtest';
        $ts = $request->header(TradingCore::TIMESTAMP_HEADER)[0] ?? '';
        $nonce = $request->header(TradingCore::NONCE_HEADER)[0] ?? '';
        $sig = TradingCore::sign(ATL208_SECRET, $ts, $nonce, 'POST', $path, $request->body());

        return $request->url() === ATL208_CORE.$path
            && $request->method() === 'POST'
            && hash_equals($sig, $request->header(TradingCore::SIGNATURE_HEADER)[0] ?? '')
            && json_decode($request->body(), true) == atl208Body();
    });
});

it('stores trading_core\'s 422 code and reason as failed', function (): void {
    Http::fake([ATL208_CORE.'/internal/backtest' => Http::response([
        'ok' => false, 'error' => 'too_many_bars', 'reason' => 'the period is 1100 1H bars per instrument; backtest.max_bars is 500',
    ], 422)]);
    $run = atl208Run($this->user);

    (new RunBacktest($run->id))->handle();

    $run->refresh();
    expect($run->status)->toBe('failed')
        ->and($run->error)->toBe('too_many_bars')
        ->and($run->error_reason)->toBe('the period is 1100 1H bars per instrument; backtest.max_bars is 500')
        ->and($run->result)->toBeNull()
        ->and($run->finished_at)->not->toBeNull();
});

it('stores a trading_core 500 as failed with its reason', function (): void {
    Http::fake([ATL208_CORE.'/internal/backtest' => Http::response(['detail' => 'Internal Server Error'], 500)]);
    $run = atl208Run($this->user);

    (new RunBacktest($run->id))->handle();

    $run->refresh();
    expect($run->status)->toBe('failed')
        ->and($run->error)->toBe('trading_core_error')
        ->and($run->error_reason)->toBe('Internal Server Error');
});

it('stores an unreachable trading_core as failed', function (): void {
    Http::fake(fn () => throw new ConnectionException('refused'));
    $run = atl208Run($this->user);

    (new RunBacktest($run->id))->handle();

    expect($run->refresh()->status)->toBe('failed')
        ->and($run->error)->toBe('trading_core_unavailable');
});

it('is never retried: one try, fails on timeout, timeout chain below retry_after, own queue', function (): void {
    $job = new RunBacktest('00000000-0000-0000-0000-000000000000');

    expect($job->tries)->toBe(1)
        ->and($job->maxExceptions)->toBe(1)
        ->and($job->failOnTimeout)->toBeTrue()
        ->and($job->queue)->toBe('backtests')
        ->and(config('atlas_defaults.backtests_api.http_timeout_seconds'))->toBeGreaterThan(config('atlas_defaults.backtest.timeout_seconds'))
        ->and($job->timeout)->toBeGreaterThan(config('atlas_defaults.backtests_api.http_timeout_seconds'))
        ->and($job->timeout)->toBeLessThan(config('queue.connections.redis.retry_after'))
        ->and(config('horizon.defaults.supervisor-backtests.queue'))->toBe(['backtests'])
        ->and(config('horizon.defaults.supervisor-backtests.tries'))->toBe(1);
});

it('a failed 422 run through the queue is not retried and sends one request', function (): void {
    Http::fake([ATL208_CORE.'/internal/backtest' => Http::response(['ok' => false, 'error' => 'no_data', 'reason' => 'no 1H candles in demo for NOPE-USDT-SWAP'], 422)]);
    config(['queue.default' => 'sync']);

    $this->actingAs($this->user, 'web')->postJson('/api/backtests', atl208Body())->assertStatus(202);

    $run = BacktestRun::query()->sole();
    expect($run->status)->toBe('failed')->and($run->error)->toBe('no_data');
    Http::assertSentCount(1);
});

it('marks a killed job failed instead of leaving it running', function (): void {
    $run = atl208Run($this->user, status: 'running');

    (new RunBacktest($run->id))->failed(new RuntimeException('timed out'));

    expect($run->refresh()->status)->toBe('failed')->and($run->error)->toBe('job_failed');
});

it('does nothing for a run that is not queued', function (): void {
    Http::fake();
    $run = atl208Run($this->user, status: 'done');

    (new RunBacktest($run->id))->handle();

    Http::assertNothingSent();
    expect($run->refresh()->status)->toBe('done');
});

it('lists the owner\'s runs in the active env, newest first, without the result', function (): void {
    $old = atl208Run($this->user, status: 'done');
    $old->forceFill(['result' => atl208Result(), 'created_at' => now()->subHour()])->save();
    $new = atl208Run($this->user);
    atl208Run($this->user, env: 'live');
    atl208Run(User::factory()->create());

    $response = $this->actingAs($this->user, 'web')->getJson('/api/backtests')->assertOk();

    expect($response->json('env'))->toBe('demo')
        ->and(array_column($response->json('data'), 'id'))->toBe([$new->id, $old->id])
        ->and($response->json('data.1'))->not->toHaveKeys(['result', 'request'])
        ->and($response->json('data.1.n_trades'))->toBe(18)
        ->and($response->json('data.1.rule_name'))->toBe('sma_10_30_cross')
        ->and($response->json('data.1.inst_ids'))->toBe(['BTC-USDT-SWAP', 'XAU-USDT-SWAP']);
});

it('shows one run with status and result', function (): void {
    $run = atl208Run($this->user, status: 'done');
    $run->forceFill(['result' => atl208Result()])->save();

    $response = $this->actingAs($this->user, 'web')
        ->getJson('/api/backtests/'.$run->id)
        ->assertOk()
        ->assertJsonPath('id', $run->id)
        ->assertJsonPath('status', 'done');

    expect($response->json('request'))->toEqual(atl208Body())
        ->and($response->json('result'))->toEqual(atl208Result());
});

it('is 404 for another env\'s run, another owner\'s run and an unknown id', function (): void {
    $live = atl208Run($this->user, env: 'live');
    $other = atl208Run(User::factory()->create());

    $this->actingAs($this->user, 'web')->getJson('/api/backtests/'.$live->id)->assertNotFound();
    $this->actingAs($this->user, 'web')->getJson('/api/backtests/'.$other->id)->assertNotFound();
    $this->actingAs($this->user, 'web')->getJson('/api/backtests/00000000-0000-0000-0000-000000000000')->assertNotFound();
    $this->actingAs($this->user, 'web')->getJson('/api/backtests/not-a-uuid')->assertNotFound();
});
