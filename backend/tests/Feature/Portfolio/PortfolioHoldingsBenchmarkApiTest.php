<?php

declare(strict_types=1);

use App\Models\User;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

// ATL-176: holdings avg_cost_usd / change_24h_pct and GET /api/portfolio/benchmark.

function hbTs(CarbonImmutable $t): string
{
    return $t->utc()->format('Y-m-d H:i:s.uP');
}

function hbInstrument(string $instId, string $type = 'SWAP', string $tick = '0.1', string $env = 'demo'): void
{
    DB::table('instruments')->insert([
        'id' => (string) Str::uuid(), 'env' => $env, 'inst_id' => $instId, 'inst_type' => $type,
        'lot_sz' => '1', 'min_sz' => '1', 'tick_sz' => $tick, 'state' => 'live', 'asset_class' => 'crypto',
        'created_at' => now(), 'updated_at' => now(),
    ]);
}

function hbFill(CarbonImmutable $at, string $instId, string $side, string $px, string $sz): void
{
    DB::table('fills')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'trade_id' => (string) Str::uuid(),
        'ord_id' => (string) random_int(1, PHP_INT_MAX), 'inst_id' => $instId, 'side' => $side,
        'fill_px' => $px, 'fill_sz' => $sz, 'fee' => '0', 'fee_ccy' => 'USDT', 'ts' => hbTs($at),
        'created_at' => now(), 'updated_at' => now(),
    ]);
}

function hbCandle(string $instId, string $bar, CarbonImmutable $at, string $close, string $env = 'demo'): void
{
    DB::table('candles')->insert([
        'env' => $env, 'inst_id' => $instId, 'bar' => $bar, 'ts' => hbTs($at),
        'o' => $close, 'h' => $close, 'l' => $close, 'c' => $close, 'vol' => '1', 'confirmed' => true,
    ]);
}

function hbBalance(CarbonImmutable $at, string $ccy, string $eq, string $eqUsd): void
{
    DB::table('balances_snapshots')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'ccy' => $ccy, 'account' => 'trading',
        'eq' => $eq, 'avail_bal' => $eq, 'frozen_bal' => '0', 'eq_usd' => $eqUsd, 'upl' => null,
        'taken_at' => hbTs($at), 'created_at' => hbTs($at), 'updated_at' => hbTs($at),
    ]);
}

function hbEquity(CarbonImmutable $at, string $equity, string $env = 'demo'): void
{
    DB::table('equity_curve')->insert([
        'id' => (string) Str::uuid(), 'env' => $env, 'ts' => hbTs($at), 'total_eq_usd' => $equity,
        'created_at' => hbTs($at), 'updated_at' => hbTs($at),
    ]);
}

/** @return array<string, mixed> */
function hbHolding(mixed $test, string $ccy): array
{
    $items = $test->getJson('/api/portfolio/holdings')->assertOk()->json('data.items');
    foreach ($items as $item) {
        if ($item['ccy'] === $ccy) {
            return $item;
        }
    }
    throw new RuntimeException("no holding {$ccy}");
}

function hbAssertStrings(mixed $value, string $path = '$'): void
{
    if (is_array($value)) {
        foreach ($value as $k => $v) {
            hbAssertStrings($v, $path.'.'.$k);
        }

        return;
    }
    expect(is_float($value) || is_int($value))->toBeFalse("number at {$path}");
}

it('requires authentication for the benchmark', function (): void {
    $this->getJson('/api/portfolio/benchmark?symbol=BTC')->assertUnauthorized();
});

describe('signed in', function (): void {
    beforeEach(function (): void {
        $this->actingAs(User::factory()->create(), 'web');
        $this->now = CarbonImmutable::now()->utc()->startOfHour();
    });

    describe('holdings avg_cost_usd (AC1)', function (): void {
        it('is the average of buys net of sells: 1 @ 60000 + 1 @ 62000, sell 1 → 61000.00', function (): void {
            hbBalance($this->now, 'BTC', '1', '61500');
            hbFill($this->now->subHours(3), 'BTC-USDT', 'buy', '60000', '1');
            hbFill($this->now->subHours(2), 'BTC-USDT', 'buy', '62000', '1');
            hbFill($this->now->subHour(), 'BTC-USDT', 'sell', '63000', '1');

            expect(hbHolding($this, 'BTC')['avg_cost_usd'])->toBe('61000.00');
        });

        it('ignores swap fills and is null with no spot fills', function (): void {
            hbBalance($this->now, 'BTC', '1', '61500');
            hbFill($this->now->subHour(), 'BTC-USDT-SWAP', 'buy', '60000', '1');

            expect(hbHolding($this, 'BTC')['avg_cost_usd'])->toBeNull();
        });

        it('starts again after the position is sold down to zero', function (): void {
            hbBalance($this->now, 'ETH', '1', '3000');
            hbFill($this->now->subHours(4), 'ETH-USDT', 'buy', '2000', '1');
            hbFill($this->now->subHours(3), 'ETH-USDT', 'sell', '2500', '1');
            hbFill($this->now->subHours(2), 'ETH-USDC', 'buy', '3000', '2');

            expect(hbHolding($this, 'ETH')['avg_cost_usd'])->toBe('3000.00');
        });

        it('keeps the spot tickSz decimals for small prices', function (): void {
            hbInstrument('PEPE-USDT', 'SPOT', '0.00000001');
            hbBalance($this->now, 'PEPE', '1000', '0.01');
            hbFill($this->now->subHours(2), 'PEPE-USDT', 'buy', '0.00001', '1000');
            hbFill($this->now->subHour(), 'PEPE-USDT', 'buy', '0.00002', '1000');

            expect(hbHolding($this, 'PEPE')['avg_cost_usd'])->toBe('0.00001500');
        });
    });

    describe('holdings change_24h_pct (AC2)', function (): void {
        it('compares the newest 1H close with the close 24 h earlier', function (): void {
            hbBalance($this->now, 'BTC', '1', '66000');
            hbInstrument('BTC-USDT-SWAP');
            hbCandle('BTC-USDT-SWAP', '1H', $this->now->subHours(30), '50000');
            hbCandle('BTC-USDT-SWAP', '1H', $this->now->subHours(24), '60000');
            hbCandle('BTC-USDT-SWAP', '1H', $this->now->subHours(12), '64000');
            hbCandle('BTC-USDT-SWAP', '1H', $this->now, '66000');

            $item = hbHolding($this, 'BTC');
            expect($item['change_24h_pct'])->toBe('10')
                ->and($item['change_24h_reason'])->toBeNull()
                ->and($item['change_24h_inst_id'])->toBe('BTC-USDT-SWAP');
        });

        it('prefers the spot instrument over the swap', function (): void {
            hbBalance($this->now, 'BTC', '1', '66000');
            hbInstrument('BTC-USDT', 'SPOT');
            hbInstrument('BTC-USDT-SWAP');
            hbCandle('BTC-USDT', '1H', $this->now->subHours(24), '50000');
            hbCandle('BTC-USDT', '1H', $this->now, '55000');
            hbCandle('BTC-USDT-SWAP', '1H', $this->now->subHours(24), '60000');
            hbCandle('BTC-USDT-SWAP', '1H', $this->now, '66000');

            $item = hbHolding($this, 'BTC');
            expect($item['change_24h_pct'])->toBe('10')->and($item['change_24h_inst_id'])->toBe('BTC-USDT');
        });

        it('falls back to 1D candles', function (): void {
            hbBalance($this->now, 'SOL', '1', '150');
            hbInstrument('SOL-USDT-SWAP');
            hbCandle('SOL-USDT-SWAP', '1D', $this->now->startOfDay()->subDay(), '200');
            hbCandle('SOL-USDT-SWAP', '1D', $this->now->startOfDay(), '150');

            expect(hbHolding($this, 'SOL')['change_24h_pct'])->toBe('-25');
        });

        it('is null with reason no_instrument when the env has no instrument for the asset', function (): void {
            hbBalance($this->now, 'ZZZ', '1', '1');
            hbInstrument('ZZZ-USDT-SWAP', 'SWAP', '0.1', 'live');

            $item = hbHolding($this, 'ZZZ');
            expect($item['change_24h_pct'])->toBeNull()->and($item['change_24h_reason'])->toBe('no_instrument');
        });

        it('is null with reason no_candles when no two closes are 24 h apart', function (): void {
            hbBalance($this->now, 'BTC', '1', '66000');
            hbInstrument('BTC-USDT-SWAP');
            hbCandle('BTC-USDT-SWAP', '1H', $this->now->subHours(5), '60000');
            hbCandle('BTC-USDT-SWAP', '1H', $this->now, '66000');

            $item = hbHolding($this, 'BTC');
            expect($item['change_24h_pct'])->toBeNull()->and($item['change_24h_reason'])->toBe('no_candles');
        });
    });

    describe('benchmark (AC3)', function (): void {
        it('starts at the first equity value in the range and scales by the candle closes', function (): void {
            hbInstrument('BTC-USDT-SWAP');
            hbEquity($this->now->subDays(40), '1');   // outside 1M
            hbEquity($this->now->subDays(10)->addMinutes(30), '5000000');
            hbEquity($this->now->subDays(5), '5100000');
            $base = $this->now->subDays(10)->startOfDay();
            hbCandle('BTC-USDT-SWAP', '4H', $base->subDays(20), '1');           // before the base, ignored
            hbCandle('BTC-USDT-SWAP', '4H', $this->now->subDays(10), '60000'); // base candle
            hbCandle('BTC-USDT-SWAP', '4H', $this->now->subDays(9), '66000');
            hbCandle('BTC-USDT-SWAP', '4H', $this->now->subDays(8), '54000');
            hbCandle('BTC-USDT-SWAP', '4H', $this->now->subDays(7), '61234.5');

            $data = $this->getJson('/api/portfolio/benchmark?symbol=BTC&range=1M')->assertOk()->json('data');

            expect($data['available'])->toBeTrue()
                ->and($data['inst_id'])->toBe('BTC-USDT-SWAP')
                ->and($data['bar'])->toBe('4H')
                ->and($data['base']['equity_usd'])->toBe('5000000')
                ->and($data['base']['close'])->toBe('60000')
                ->and(array_column($data['points'], 'value_usd'))->toBe(['5000000', '5500000', '4500000', '5102875'])
                ->and($data['points'][0]['ts'])->toBe($this->now->subDays(10)->addMinutes(30)->toIso8601ZuluString('microsecond'));
            hbAssertStrings(array_map(fn ($p) => [$p['close'], $p['value_usd']], $data['points']));
        });

        it('uses the demo equivalent when the USDT perp is missing', function (): void {
            hbInstrument('SPY-USDC-SWAP');
            hbEquity($this->now->subDays(2), '100');
            hbCandle('SPY-USDC-SWAP', '1D', $this->now->subDays(3), '500');
            hbCandle('SPY-USDC-SWAP', '1D', $this->now->subDay(), '550');

            $data = $this->getJson('/api/portfolio/benchmark?symbol=SPY&range=ALL')->assertOk()->json('data');

            expect($data['inst_id'])->toBe('SPY-USDC-SWAP')
                ->and(array_column($data['points'], 'value_usd'))->toBe(['100', '110']);
        });

        it('returns unavailable when the env universe lacks the symbol', function (): void {
            hbInstrument('XAU-USDT-SWAP', 'SWAP', '0.1', 'live');
            hbEquity($this->now->subDays(2), '100');

            $data = $this->getJson('/api/portfolio/benchmark?symbol=XAU&range=ALL')->assertOk()->json('data');

            expect($data['available'])->toBeFalse()
                ->and($data['reason'])->toBe('not_in_universe')
                ->and($data['points'])->toBe([]);
        });

        it('returns unavailable with no_equity or no_candles', function (): void {
            hbInstrument('BTC-USDT-SWAP');
            expect($this->getJson('/api/portfolio/benchmark?symbol=BTC&range=ALL')->json('data.reason'))->toBe('no_equity');

            hbEquity($this->now->subDays(2), '100');
            expect($this->getJson('/api/portfolio/benchmark?symbol=BTC&range=ALL')->json('data.reason'))->toBe('no_candles');
        });

        it('validates symbol and range', function (): void {
            $this->getJson('/api/portfolio/benchmark')->assertUnprocessable()->assertJsonValidationErrors('symbol');
            $this->getJson('/api/portfolio/benchmark?symbol=ETH')->assertUnprocessable()->assertJsonValidationErrors('symbol');
            $this->getJson('/api/portfolio/benchmark?symbol=BTC&range=2Y')->assertUnprocessable()->assertJsonValidationErrors('range');
        });
    });

    it('returns every holdings and benchmark value as a decimal string (AC4)', function (): void {
        hbBalance($this->now, 'BTC', '1.5', '99000');
        hbInstrument('BTC-USDT-SWAP');
        hbFill($this->now->subHours(2), 'BTC-USDT', 'buy', '60000.5', '1.5');
        hbCandle('BTC-USDT-SWAP', '1H', $this->now->subHours(24), '60000');
        hbCandle('BTC-USDT-SWAP', '1H', $this->now->subHours(4), '64000');
        hbCandle('BTC-USDT-SWAP', '1H', $this->now, '66000');
        hbEquity($this->now->subHours(5), '5000000');

        $item = hbHolding($this, 'BTC');
        foreach (['quantity', 'value_usd', 'pct_of_portfolio', 'avg_cost_usd', 'change_24h_pct'] as $key) {
            expect($item[$key])->toBeString()->toMatch('/^-?\d+(\.\d+)?$/');
        }
        expect($item['avg_cost_usd'])->toBe('60000.50');

        $data = $this->getJson('/api/portfolio/benchmark?symbol=BTC&range=1D')->assertOk()->json('data');
        expect($data['bar'])->toBe('1H');
        hbAssertStrings($data);
        foreach ($data['points'] as $point) {
            expect($point['value_usd'])->toMatch('/^-?\d+(\.\d+)?$/')->and($point['close'])->toMatch('/^-?\d+(\.\d+)?$/');
        }
        expect($data['base']['equity_usd'])->toBe('5000000');
    });
});
