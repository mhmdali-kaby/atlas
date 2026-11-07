<?php

declare(strict_types=1);

use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

const PORTFOLIO_TABLES = [
    'instruments',
    'balances_snapshots',
    'positions_snapshots',
    'bills',
    'deposits_withdrawals',
    'sync_status',
    'equity_curve',
];

it('creates every portfolio and instrument table', function (): void {
    $found = collect(DB::select(
        'SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ANY (?)',
        ['{'.implode(',', PORTFOLIO_TABLES).'}'],
    ))->pluck('table_name')->sort()->values()->all();

    expect($found)->toEqual(collect(PORTFOLIO_TABLES)->sort()->values()->all());
});

it('has no floating-point column in the portfolio tables', function (): void {
    $floats = DB::select(
        "SELECT table_name, column_name, data_type FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = ANY (?)
           AND data_type IN ('double precision', 'real')",
        ['{'.implode(',', PORTFOLIO_TABLES).'}'],
    );

    expect($floats)->toBe([]);
});

it('stores every price, size and amount column as numeric', function (): void {
    $moneyColumns = [
        'instruments' => ['ct_val', 'ct_mult', 'lot_sz', 'min_sz', 'tick_sz', 'max_lever'],
        'balances_snapshots' => ['eq', 'avail_bal', 'frozen_bal', 'eq_usd', 'upl'],
        'positions_snapshots' => ['pos', 'avg_px', 'mark_px', 'liq_px', 'lever', 'upl', 'upl_ratio', 'margin'],
        'bills' => ['bal_chg', 'pnl', 'fee'],
        'deposits_withdrawals' => ['amount'],
        'equity_curve' => ['total_eq_usd', 'upl_usd'],
    ];

    foreach ($moneyColumns as $table => $columns) {
        $types = collect(DB::select(
            'SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?',
            [$table],
        ))->pluck('data_type', 'column_name');

        foreach ($columns as $column) {
            expect($types[$column] ?? null)->toBe('numeric', "{$table}.{$column}");
        }
    }
});

it('makes equity_curve a TimescaleDB hypertable', function (): void {
    $names = collect(DB::select('SELECT hypertable_name FROM timescaledb_information.hypertables'))
        ->pluck('hypertable_name')->all();

    expect($names)->toContain('equity_curve');
});

it('rejects two instruments with the same env and inst_id', function (): void {
    $row = fn (): array => [
        'id' => (string) Str::uuid(),
        'env' => 'demo',
        'inst_id' => 'BTC-USDT-SWAP',
        'inst_type' => 'SWAP',
        'lot_sz' => '0.01',
        'min_sz' => '0.01',
        'tick_sz' => '0.1',
        'state' => 'live',
        'asset_class' => 'crypto',
    ];

    DB::table('instruments')->insert($row());
    DB::table('instruments')->insert([...$row(), 'env' => 'live']);

    expect(fn () => DB::table('instruments')->insert($row()))
        ->toThrow(UniqueConstraintViolationException::class);
});

it('keeps decimal precision on instrument sizes', function (): void {
    DB::table('instruments')->insert([
        'id' => (string) Str::uuid(),
        'env' => 'demo',
        'inst_id' => 'XAU-USDT-SWAP',
        'inst_type' => 'SWAP',
        'lot_sz' => '0.000000000000000001',
        'min_sz' => '1',
        'tick_sz' => '0.01',
        'state' => 'live',
    ]);

    $lot = DB::table('instruments')->where('inst_id', 'XAU-USDT-SWAP')->value('lot_sz');

    expect((string) $lot)->toBe('0.000000000000000001');
});
