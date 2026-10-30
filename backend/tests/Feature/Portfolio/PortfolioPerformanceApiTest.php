<?php

declare(strict_types=1);

use App\Models\User;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

// ATL-174: GET /api/portfolio/performance.

function ppTs(string $utc): string
{
    return CarbonImmutable::parse($utc, 'UTC')->format('Y-m-d H:i:s.uP');
}

function ppProposal(string $instId, string $entry, string $stop): string
{
    $id = (string) Str::uuid();
    DB::table('proposals')->insert([
        'id' => $id, 'env' => 'demo', 'author' => 'strategist', 'inst_id' => $instId, 'side' => 'buy',
        'order_type' => 'limit', 'entry' => $entry, 'stop' => $stop, 'status' => 'closed',
        'created_at' => now(), 'updated_at' => now(),
    ]);

    return $id;
}

/**
 * @param  array<string, mixed>  $overrides
 */
function ppClosed(string $closedAtUtc, string $exit, string $realised, array $overrides = []): void
{
    DB::table('positions_closed')->insert(array_merge([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'pos_id' => (string) random_int(1, PHP_INT_MAX),
        'inst_id' => 'BTC-USDT-SWAP', 'inst_type' => 'SWAP', 'mgn_mode' => 'isolated', 'pos_side' => 'net',
        'side' => 'long', 'close_type' => '2', 'lever' => '5', 'ccy' => 'USDT', 'size' => '1',
        'entry_px' => '100', 'exit_px' => $exit, 'realised_pnl' => $realised, 'pnl' => $realised,
        'fee' => '0', 'funding_fee' => '0', 'liq_penalty' => '0', 'pnl_ratio' => '0',
        'opened_at' => ppTs($closedAtUtc.' -2 hours'), 'closed_at' => ppTs($closedAtUtc),
        'origin' => 'external', 'created_at' => now(), 'updated_at' => now(),
    ], $overrides));
}

function ppEquity(string $tsUtc, string $equity): void
{
    DB::table('equity_curve')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'ts' => ppTs($tsUtc), 'total_eq_usd' => $equity,
        'created_at' => now(), 'updated_at' => now(),
    ]);
}

/** One equity point per day at 12:00 UTC (15:00 Baghdad), starting 2025-05-01. */
function ppDailyEquity(array $values): void
{
    foreach ($values as $i => $v) {
        ppEquity(CarbonImmutable::parse('2025-05-01 12:00:00', 'UTC')->addDays($i)->toDateTimeString(), (string) $v);
    }
}

/** @return array<string, mixed> */
function ppGet(mixed $test, string $query = ''): array
{
    return $test->getJson('/api/portfolio/performance'.$query)->assertOk()->json('data');
}

beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
});

it('requires auth', function (): void {
    auth()->guard('web')->logout();
    $this->getJson('/api/portfolio/performance')->assertUnauthorized();
});

it('validates the range and accepts All in any case', function (): void {
    $this->getJson('/api/portfolio/performance?range=1D')->assertUnprocessable();
    $this->getJson('/api/portfolio/performance?range=All')->assertOk()->assertJsonPath('data.range', 'ALL');
    $this->getJson('/api/portfolio/performance?range=3M')->assertOk()->assertJsonPath('data.range', '3M');
});

it('returns nulls with reasons and zero samples on an empty account', function (): void {
    $d = ppGet($this);
    expect($d['win_rate_pct'])->toBe(['value' => null, 'sample_size' => 0, 'reason' => 'no_trades'])
        ->and($d['profit_factor']['reason'])->toBe('no_trades')
        ->and($d['expectancy_r']['reason'])->toBe('no_trades')
        ->and($d['max_drawdown']['pct'])->toBeNull()
        ->and($d['max_drawdown']['reason'])->toBe('insufficient_data')
        ->and($d['sharpe']['reason'])->toBe('insufficient_data')
        ->and($d['breakdowns']['by_weekday'])->toHaveCount(7)
        ->and($d['breakdowns']['by_hour'])->toHaveCount(24)
        ->and($d['breakdowns']['heatmap'])->toBe([]);
});

it('computes win rate, averages, profit factor and expectancy on 4 wins and 2 losses', function (): void {
    // Long, entry 100, proposal stop 90 → 1R = 10 per unit.
    $p = ppProposal('BTC-USDT-SWAP', '100', '90');
    $atlas = ['proposal_id' => $p, 'agent' => 'strategist', 'origin' => 'atlas'];
    ppClosed('2025-06-02 10:00:00', '120', '20', $atlas);   // +2.00R
    ppClosed('2025-06-02 11:00:00', '115', '15', $atlas);   // +1.50R
    ppClosed('2025-06-03 10:00:00', '110', '10', $atlas);   // +1.00R
    ppClosed('2025-06-03 11:00:00', '125', '25', $atlas);   // +2.50R
    ppClosed('2025-06-04 10:00:00', '90', '-10', $atlas);   // −1.00R
    ppClosed('2025-06-04 11:00:00', '95', '-5', $atlas);    // −0.50R

    $d = ppGet($this, '?range=ALL');

    // 4 / 6 = 66.67 %. Gross profit 70, gross loss 15 → PF 70/15 = 4.6667.
    // Expectancy = (2 + 1.5 + 1 + 2.5 − 1 − 0.5) / 6 = 5.5 / 6 = 0.9167 R.
    expect($d['win_rate_pct'])->toBe(['value' => '66.67', 'sample_size' => 6, 'reason' => null])
        ->and($d['profit_factor'])->toBe(['value' => '4.6667', 'sample_size' => 6, 'reason' => null])
        ->and($d['expectancy_r'])->toBe(['value' => '0.9167', 'sample_size' => 6, 'reason' => null])
        ->and($d['avg_win_usd'])->toBe(['value' => '17.5', 'sample_size' => 4, 'reason' => null])
        ->and($d['avg_loss_usd'])->toBe(['value' => '-7.5', 'sample_size' => 2, 'reason' => null])
        ->and($d['trades'])->toMatchArray([
            'total' => 6, 'wins' => 4, 'losses' => 2, 'flats' => 0, 'excluded_non_usd' => 0,
            'gross_profit_usd' => '70', 'gross_loss_usd' => '-15', 'net_pnl_usd' => '55',
        ])
        ->and($d['breakdowns']['by_agent'])->toBe([
            ['agent' => 'strategist', 'pnl_usd' => '55', 'trades' => 6, 'wins' => 4, 'losses' => 2],
        ]);
});

it('excludes flats from the win rate, external trades from expectancy and coin-margined trades from everything', function (): void {
    ppClosed('2025-06-02 10:00:00', '120', '20');               // external: no stop → no R
    ppClosed('2025-06-02 11:00:00', '100', '0');                // flat
    ppClosed('2025-06-02 12:00:00', '90', '-0.1', ['ccy' => 'BTC']); // coin PnL

    $d = ppGet($this);

    expect($d['win_rate_pct'])->toBe(['value' => '100.00', 'sample_size' => 1, 'reason' => null])
        ->and($d['profit_factor'])->toBe(['value' => null, 'sample_size' => 1, 'reason' => 'no_losses'])
        ->and($d['expectancy_r'])->toBe(['value' => null, 'sample_size' => 0, 'reason' => 'no_r'])
        ->and($d['trades']['flats'])->toBe(1)
        ->and($d['trades']['excluded_non_usd'])->toBe(1)
        ->and($d['breakdowns']['by_agent'][0]['agent'])->toBeNull();
});

it('finds a 10.00 % max drawdown from peak 110 to trough 99', function (): void {
    ppEquity('2025-05-01 00:00:00', '100');
    ppEquity('2025-05-01 00:01:00', '110');
    ppEquity('2025-05-01 00:02:00', '105');
    ppEquity('2025-05-01 00:03:00', '99');
    ppEquity('2025-05-01 00:04:00', '108');
    ppEquity('2025-05-01 00:05:00', '104'); // smaller fall from 110 later

    $dd = ppGet($this)['max_drawdown'];

    expect($dd['pct'])->toBe('10.00')
        ->and($dd['usd'])->toBe('11')
        ->and($dd['peak_usd'])->toBe('110')
        ->and($dd['trough_usd'])->toBe('99')
        ->and($dd['peak_at'])->toBe('2025-05-01T00:01:00.000000Z')
        ->and($dd['trough_at'])->toBe('2025-05-01T00:03:00.000000Z')
        ->and($dd['sample_size'])->toBe(6)
        ->and($dd['reason'])->toBeNull();
});

it('reports a zero drawdown on a curve that only rises', function (): void {
    ppEquity('2025-05-01 00:00:00', '100');
    ppEquity('2025-05-01 00:01:00', '101');

    expect(ppGet($this)['max_drawdown'])->toMatchArray(['usd' => '0', 'pct' => '0.00', 'sample_size' => 2]);
});

it('returns null Sharpe and Sortino with insufficient_data on 7 days of equity', function (): void {
    ppDailyEquity([1000, 1012, 1003.9, 1008.92, 1011.95, 1000.82, 1007.83]);

    $d = ppGet($this);

    expect($d['sharpe'])->toBe(['value' => null, 'sample_size' => 7, 'reason' => 'insufficient_data'])
        ->and($d['sortino'])->toBe(['value' => null, 'sample_size' => 7, 'reason' => 'insufficient_data']);
});

it('computes daily Sharpe and Sortino on 30 days of equity', function (): void {
    // Daily returns cycle through +1.2, −0.8, +0.5, +0.3, −1.1, +0.7, +0.9, −0.4, +0.2, −0.6 %.
    // Hand-computed (sample stdev n−1; downside deviation √(Σ min(r,0)² / n); × √365):
    // Sharpe 2.89989… → 2.8999, Sortino 4.50579… → 4.5058.
    ppDailyEquity([
        1000, 1012.0, 1003.9, 1008.92, 1011.95, 1000.82, 1007.83, 1016.9, 1012.83, 1014.86,
        1008.77, 1020.88, 1012.71, 1017.77, 1020.82, 1009.59, 1016.66, 1025.81, 1021.71, 1023.75,
        1017.61, 1029.82, 1021.58, 1026.69, 1029.77, 1018.44, 1025.57, 1034.8, 1030.66, 1032.72,
    ]);
    // An earlier intraday point does not change a day's close (last point of the Baghdad day).
    ppEquity('2025-05-03 06:00:00', '5000');

    $d = ppGet($this);

    expect($d['sharpe'])->toBe(['value' => '2.8999', 'sample_size' => 30, 'reason' => null])
        ->and($d['sortino'])->toBe(['value' => '4.5058', 'sample_size' => 30, 'reason' => null]);
});

it('does not count a deposit as a daily return', function (): void {
    $values = [];
    for ($i = 0; $i < 12; $i++) {
        $values[] = 1000 + ($i % 2 === 0 ? 0 : 10);
    }
    ppDailyEquity($values);
    $base = ppGet($this)['sharpe']['value'];

    // Shift every close from day 7 on by +500, first without and then with the matching deposit.
    DB::table('equity_curve')->where('ts', '>=', ppTs('2025-05-07 00:00:00'))->update(['total_eq_usd' => DB::raw('total_eq_usd + 500')]);
    $noFlow = ppGet($this)['sharpe']['value'];
    DB::table('deposits_withdrawals')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'kind' => 'deposit', 'external_id' => 'dep-1', 'ccy' => 'USDT',
        'amount' => '500', 'state' => '2', 'ts' => ppTs('2025-05-07 08:00:00'), 'created_at' => now(), 'updated_at' => now(),
    ]);
    $withFlow = ppGet($this)['sharpe']['value'];

    expect($base)->not->toBeNull()->and($noFlow)->not->toBeNull()->and($withFlow)->not->toBeNull();
    // Without the deposit the +50 % day dominates; with it the ratio is back near the base (closes after it sit on a larger equity, so not equal).
    expect(abs((float) $withFlow - (float) $base))->toBeLessThan(abs((float) $noFlow - (float) $base) / 10);
});

it('buckets a trade closed at 21:30 UTC into 00:00 Baghdad of the next weekday', function (): void {
    // Wednesday 2025-06-04 21:30 UTC = Thursday 2025-06-05 00:30 Asia/Baghdad (UTC+3).
    ppClosed('2025-06-04 21:30:00', '120', '20', ['inst_id' => 'XAU-USDT-SWAP']);

    $b = ppGet($this)['breakdowns'];

    expect($b['heatmap'])->toBe([['weekday' => 4, 'hour' => 0, 'pnl_usd' => '20', 'trades' => 1, 'wins' => 1, 'losses' => 0]])
        ->and($b['by_hour'][0])->toBe(['hour' => 0, 'pnl_usd' => '20', 'trades' => 1, 'wins' => 1, 'losses' => 0])
        ->and($b['by_hour'][21]['trades'])->toBe(0)
        ->and($b['by_weekday'][3])->toBe(['weekday' => 4, 'pnl_usd' => '20', 'trades' => 1, 'wins' => 1, 'losses' => 0])
        ->and($b['by_weekday'][2]['trades'])->toBe(0);
});

it('breaks PnL down by instrument and asset class, best first', function (): void {
    DB::table('instruments')->insert([
        ['id' => (string) Str::uuid(), 'env' => 'demo', 'inst_id' => 'XAU-USDT-SWAP', 'inst_type' => 'SWAP', 'lot_sz' => '1', 'min_sz' => '1', 'tick_sz' => '0.1', 'state' => 'live', 'asset_class' => 'commodity', 'created_at' => now(), 'updated_at' => now()],
        ['id' => (string) Str::uuid(), 'env' => 'demo', 'inst_id' => 'BTC-USDT-SWAP', 'inst_type' => 'SWAP', 'lot_sz' => '1', 'min_sz' => '1', 'tick_sz' => '0.1', 'state' => 'live', 'asset_class' => 'crypto', 'created_at' => now(), 'updated_at' => now()],
    ]);
    ppClosed('2025-06-02 10:00:00', '120', '20', ['inst_id' => 'XAU-USDT-SWAP']);
    ppClosed('2025-06-02 11:00:00', '90', '-10');
    ppClosed('2025-06-02 12:00:00', '130', '30');
    ppClosed('2025-06-02 13:00:00', '130', '7', ['inst_id' => 'NEW-USDT-SWAP']); // no instruments row

    $b = ppGet($this)['breakdowns'];

    expect(array_column($b['by_instrument'], 'inst_id'))->toBe(['BTC-USDT-SWAP', 'XAU-USDT-SWAP', 'NEW-USDT-SWAP']) // tie at 20 → by key
        ->and($b['by_instrument'][0])->toBe(['inst_id' => 'BTC-USDT-SWAP', 'pnl_usd' => '20', 'trades' => 2, 'wins' => 1, 'losses' => 1])
        ->and($b['by_asset_class'])->toBe([
            ['asset_class' => 'commodity', 'pnl_usd' => '20', 'trades' => 1, 'wins' => 1, 'losses' => 0],
            ['asset_class' => 'crypto', 'pnl_usd' => '20', 'trades' => 2, 'wins' => 1, 'losses' => 1],
            ['asset_class' => 'other', 'pnl_usd' => '7', 'trades' => 1, 'wins' => 1, 'losses' => 0],
        ]);
});

it('limits trades and equity to the range and the env', function (): void {
    CarbonImmutable::setTestNow('2025-06-30 12:00:00');
    ppClosed('2025-06-20 10:00:00', '120', '20');
    ppClosed('2025-04-01 10:00:00', '90', '-10');
    ppClosed('2025-06-21 10:00:00', '90', '-10', ['env' => 'live']);

    $d = ppGet($this, '?range=1M');
    expect($d['trades']['total'])->toBe(1)->and($d['from'])->toBe('2025-05-30T12:00:00.000000Z');
    expect(ppGet($this, '?range=ALL')['trades']['total'])->toBe(2);
    expect(ppGet($this, '?env=live')['trades']['total'])->toBe(1);
    CarbonImmutable::setTestNow();
});
