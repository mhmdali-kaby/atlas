<?php

declare(strict_types=1);

use App\Models\User;
use Brick\Math\BigDecimal;
use Carbon\CarbonImmutable;
use Database\Seeders\PortfolioDemoSeeder;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

function ts(CarbonImmutable $t): string
{
    return $t->utc()->format('Y-m-d H:i:s.uP');
}

function equityPoint(string $env, CarbonImmutable $at, string $equity): void
{
    DB::table('equity_curve')->insert([
        'id' => (string) Str::uuid(), 'env' => $env, 'ts' => ts($at), 'total_eq_usd' => $equity,
        'created_at' => ts($at), 'updated_at' => ts($at),
    ]);
}

function cashflowRow(string $env, string $kind, string $amount, CarbonImmutable $at, string $state = '2', string $ccy = 'USDT'): void
{
    DB::table('deposits_withdrawals')->insert([
        'id' => (string) Str::uuid(), 'env' => $env, 'kind' => $kind, 'external_id' => (string) Str::uuid(),
        'ccy' => $ccy, 'amount' => $amount, 'state' => $state, 'ts' => ts($at),
        'created_at' => ts($at), 'updated_at' => ts($at),
    ]);
}

/**
 * Asserts every value in a decoded JSON tree is a string, bool, int, array or null — never a float.
 */
function assertNoFloats(mixed $value, string $path = '$'): void
{
    if (is_array($value)) {
        foreach ($value as $k => $v) {
            assertNoFloats($v, $path.'.'.$k);
        }

        return;
    }
    expect(is_float($value))->toBeFalse("float at {$path}");
}

const PORTFOLIO_ENDPOINTS = [
    '/api/portfolio/summary',
    '/api/portfolio/equity?range=ALL',
    '/api/portfolio/holdings',
    '/api/portfolio/positions',
    '/api/portfolio/cashflows',
];

it('returns 401 on every endpoint without a session', function (string $url): void {
    $this->getJson($url)->assertUnauthorized();
})->with(PORTFOLIO_ENDPOINTS);

describe('signed in', function (): void {
    beforeEach(function (): void {
        $this->actingAs(User::factory()->create(), 'web');
    });

    it('answers every endpoint with no float anywhere after the demo seeder', function (string $url): void {
        $this->seed(PortfolioDemoSeeder::class);
        $response = $this->getJson($url)->assertOk();
        assertNoFloats($response->json());
    })->with(PORTFOLIO_ENDPOINTS);

    it('serves the summary from the seeded data with decision #238 total PnL', function (): void {
        $this->seed(PortfolioDemoSeeder::class);

        $data = $this->getJson('/api/portfolio/summary')->assertOk()->json('data');

        foreach (['total_equity_usd', 'net_deposits_usd', 'total_pnl_usd', 'total_pnl_pct', 'pnl_since',
            'today_pnl_usd', 'unrealised_pnl_usd', 'fees_paid_usd', 'funding_net_usd', 'reconciled_at'] as $key) {
            expect($data)->toHaveKey($key);
            expect($data[$key] === null || is_string($data[$key]))->toBeTrue("{$key} is string|null");
        }
        expect($data['stale'])->toBeFalse();

        $first = DB::table('equity_curve')->where('env', 'demo')->orderBy('ts')->first();
        $last = DB::table('equity_curve')->where('env', 'demo')->orderByDesc('ts')->first();
        $expected = BigDecimal::of($last->total_eq_usd)->minus(BigDecimal::of($first->total_eq_usd)->plus('0'));

        expect(BigDecimal::of($data['total_pnl_usd'])->isEqualTo($expected))->toBeTrue()
            ->and(BigDecimal::of($data['total_equity_usd'])->isEqualTo($last->total_eq_usd))->toBeTrue()
            ->and($data['net_deposits_usd'])->toBe('0')
            ->and($data['pnl_since'])->toBe(CarbonImmutable::parse($first->ts)->utc()->toIso8601ZuluString('microsecond'))
            // Fees: 5.4836 + 8.40631; funding: 1.09672 + 0.82254 − 0.27418 − 1.681263.
            ->and($data['fees_paid_usd'])->toBe('13.88991')
            ->and($data['funding_net_usd'])->toBe('-0.036183')
            // BTC (68120.3 − 67250.5) × 0.25 + XAU (2728.4 − 2741.8) × −4.
            ->and($data['unrealised_pnl_usd'])->toBe('271.05');
    });

    it('adds deposits after the baseline to the invested amount, but not earlier ones', function (): void {
        CarbonImmutable::setTestNow('2026-10-28 12:00:00');
        $now = CarbonImmutable::now();
        cashflowRow('demo', 'deposit', '500', $now->subDays(3)); // before baseline: not invested
        equityPoint('demo', $now->subDays(2), '1000');
        cashflowRow('demo', 'deposit', '200', $now->subDay());
        cashflowRow('demo', 'withdrawal', '50', $now->subHours(12));
        cashflowRow('demo', 'deposit', '999', $now->subHours(6), state: '0'); // pending: ignored
        equityPoint('demo', $now->subMinute(), '1300');

        $data = $this->getJson('/api/portfolio/summary')->assertOk()->json('data');

        // 1300 − (1000 + 200 − 50) = 150; 150 / 1150 × 100.
        expect($data['net_deposits_usd'])->toBe('650')
            ->and($data['total_pnl_usd'])->toBe('150')
            ->and($data['total_pnl_pct'])->toBe('13.0435');
    });

    it('returns a null total_pnl_pct when the baseline is 0', function (): void {
        CarbonImmutable::setTestNow('2026-10-28 12:00:00');
        $now = CarbonImmutable::now();
        equityPoint('demo', $now->subDay(), '0');
        equityPoint('demo', $now->subMinute(), '25.5');

        $data = $this->getJson('/api/portfolio/summary')->assertOk()->json('data');

        expect($data['total_pnl_usd'])->toBe('25.5')
            ->and($data['total_pnl_pct'])->toBeNull();
    });

    it('returns nulls, not errors, before anything has synced', function (): void {
        $data = $this->getJson('/api/portfolio/summary')->assertOk()->json('data');

        expect($data['total_equity_usd'])->toBeNull()
            ->and($data['total_pnl_usd'])->toBeNull()
            ->and($data['pnl_since'])->toBeNull()
            ->and($data['today_pnl_usd'])->toBeNull()
            ->and($data['unrealised_pnl_usd'])->toBeNull()
            ->and($data['reconciled_at'])->toBeNull()
            ->and($data['stale'])->toBeTrue();
    });

    it('measures today_pnl from the first point at or after 00:00 Asia/Baghdad', function (): void {
        $this->travelTo(CarbonImmutable::parse('2026-10-28 21:30:00', 'UTC')); // 00:30 Baghdad, 29 Oct
        equityPoint('demo', CarbonImmutable::parse('2026-10-28 00:00:00', 'UTC'), '1000');
        equityPoint('demo', CarbonImmutable::parse('2026-10-28 20:59:00', 'UTC'), '1100');
        equityPoint('demo', CarbonImmutable::parse('2026-10-28 21:00:00', 'UTC'), '1200');
        equityPoint('demo', CarbonImmutable::parse('2026-10-28 21:29:00', 'UTC'), '1250.75');

        expect($this->getJson('/api/portfolio/summary')->assertOk()->json('data.today_pnl_usd'))->toBe('50.75');
    });

    it('marks the summary stale when reconciled_at is 4 minutes old', function (int $minutes, bool $stale): void {
        CarbonImmutable::setTestNow('2026-10-28 12:00:00');
        DB::table('sync_status')->insert([
            'id' => (string) Str::uuid(), 'env' => 'demo', 'stream' => 'balances',
            'reconciled_at' => ts(CarbonImmutable::now()->subMinutes($minutes)),
        ]);

        expect($this->getJson('/api/portfolio/summary')->assertOk()->json('data.stale'))->toBe($stale);
    })->with([[4, true], [2, false]]);

    it('downsamples 50,000 points to at most 500 in ascending order for range=ALL', function (): void {
        CarbonImmutable::setTestNow('2026-10-28 12:00:00');
        DB::statement(
            "INSERT INTO equity_curve (id, env, ts, total_eq_usd)
             SELECT gen_random_uuid(), 'demo', timestamptz '2026-10-28 12:00:00+00' - make_interval(mins => g), 5000000 + g
             FROM generate_series(0, 49999) g",
        );

        $data = $this->getJson('/api/portfolio/equity?range=ALL')->assertOk()->json('data');
        $ts = array_column($data['points'], 'ts');

        expect(count($ts))->toBeLessThanOrEqual(500)->toBeGreaterThan(400)
            ->and($ts)->toBe(collect($ts)->sort()->values()->all())
            ->and(count(array_unique($ts)))->toBe(count($ts))
            ->and(end($data['points'])['equity_usd'])->toBe('5000000')
            ->and($data['from'])->toBeNull();
    });

    it('returns only the last 24 hours for range=1D, with deposit markers in range', function (): void {
        CarbonImmutable::setTestNow('2026-10-28 12:00:00');
        $now = CarbonImmutable::now();
        DB::statement(
            "INSERT INTO equity_curve (id, env, ts, total_eq_usd)
             SELECT gen_random_uuid(), 'demo', timestamptz '2026-10-28 12:00:00+00' - make_interval(mins => g * 10), 1000
             FROM generate_series(0, 431) g", // 3 days of 10-minute points
        );
        cashflowRow('demo', 'deposit', '100', $now->subHours(5));
        cashflowRow('demo', 'withdrawal', '40', $now->subDays(2));

        $data = $this->getJson('/api/portfolio/equity?range=1D')->assertOk()->json('data');
        $cutoff = $now->subDay();

        expect($data['points'])->toHaveCount(145); // 24h of 10-minute points, both ends included
        foreach ($data['points'] as $p) {
            expect(CarbonImmutable::parse($p['ts'])->gte($cutoff))->toBeTrue();
        }
        expect($data['markers'])->toHaveCount(1)
            ->and($data['markers'][0]['kind'])->toBe('deposit')
            ->and($data['markers'][0]['amount_usd'])->toBe('100');

        $this->getJson('/api/portfolio/equity?range=5Y')->assertUnprocessable()->assertJsonValidationErrors('range');
    });

    it('returns the two seeded positions with sides and decimal-string sizes', function (): void {
        $this->seed(PortfolioDemoSeeder::class);

        $items = collect($this->getJson('/api/portfolio/positions')->assertOk()->json('data.items'))->keyBy('inst_id');

        expect($items)->toHaveCount(2)
            ->and($items['BTC-USDT-SWAP']['side'])->toBe('long')
            ->and($items['BTC-USDT-SWAP']['size'])->toBe('25')
            ->and($items['BTC-USDT-SWAP']['entry'])->toBe('67250.5')
            ->and($items['BTC-USDT-SWAP']['upl_usd'])->toBe('217.45')
            ->and($items['BTC-USDT-SWAP']['leverage'])->toBe('5')
            ->and($items['BTC-USDT-SWAP']['margin_mode'])->toBe('isolated')
            ->and($items['BTC-USDT-SWAP']['stop'])->toBeNull()
            ->and($items['BTC-USDT-SWAP']['proposal_id'])->toBeNull()
            ->and($items['XAU-USDT-SWAP']['side'])->toBe('short')
            ->and($items['XAU-USDT-SWAP']['size'])->toBe('400')
            ->and($items['XAU-USDT-SWAP']['upl_usd'])->toBe('53.6');
        expect(array_key_exists('take_profits', $items['XAU-USDT-SWAP']))->toBeTrue()
            ->and(array_key_exists('agent', $items['XAU-USDT-SWAP']))->toBeTrue();
    });

    it('returns holdings whose pct_of_portfolio sums to 100 within 0.01', function (): void {
        $this->seed(PortfolioDemoSeeder::class);

        $data = $this->getJson('/api/portfolio/holdings')->assertOk()->json('data');
        $sum = BigDecimal::zero();
        foreach ($data['items'] as $item) {
            expect($item['pct_of_portfolio'])->toBeString()
                ->and($item['avg_cost_usd'])->toBeNull()
                ->and($item['change_24h_pct'])->toBeNull();
            $sum = $sum->plus($item['pct_of_portfolio']);
        }

        expect($data['items'])->toHaveCount(3)
            ->and($sum->minus(100)->abs()->isLessThanOrEqualTo('0.01'))->toBeTrue()
            ->and(BigDecimal::of($data['total_value_usd'])->isEqualTo(
                $this->getJson('/api/portfolio/summary')->json('data.total_equity_usd'),
            ))->toBeTrue();
    });

    it('paginates cash flows newest first and filters by kind', function (): void {
        CarbonImmutable::setTestNow('2026-10-28 12:00:00');
        $now = CarbonImmutable::now();
        for ($i = 1; $i <= 5; $i++) {
            cashflowRow('demo', $i % 2 === 0 ? 'withdrawal' : 'deposit', (string) ($i * 10), $now->subDays($i));
        }
        cashflowRow('live', 'deposit', '1', $now);

        $page = $this->getJson('/api/portfolio/cashflows?per_page=2')->assertOk();
        expect($page->json('meta.total'))->toBe(5)
            ->and($page->json('data'))->toHaveCount(2)
            ->and($page->json('data.0.amount'))->toBe('10')
            ->and($page->json('data.1.kind'))->toBe('withdrawal');

        expect($this->getJson('/api/portfolio/cashflows?kind=withdrawal')->json('meta.total'))->toBe(2)
            ->and($this->getJson('/api/portfolio/cashflows?env=live')->json('meta.total'))->toBe(1);
    });

    it('keeps demo and live apart', function (): void {
        $this->seed(PortfolioDemoSeeder::class);

        expect($this->getJson('/api/portfolio/positions?env=live')->json('data.items'))->toBe([])
            ->and($this->getJson('/api/portfolio/summary?env=live')->json('data.total_equity_usd'))->toBeNull();
    });
});

it('refuses to seed when APP_ENV=production', function (): void {
    $this->app['env'] = 'production';

    expect(fn () => (new PortfolioDemoSeeder)->run())->toThrow(RuntimeException::class);
    expect(DB::table('equity_curve')->count())->toBe(0);
});
