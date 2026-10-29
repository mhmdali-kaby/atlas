<?php

declare(strict_types=1);

use Illuminate\Database\QueryException;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

const MARKET_HYPERTABLES = ['candles', 'funding_rates', 'open_interest', 'long_short_ratio', 'taker_volume'];

/** @return array<string, string> column_name => data_type */
function marketColumnTypes(string $table): array
{
    return collect(DB::select(
        'SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?',
        [$table],
    ))->pluck('data_type', 'column_name')->all();
}

/**
 * @param  array<string, mixed>  $overrides
 * @return array<string, mixed>
 */
function backfillJob(array $overrides = []): array
{
    return [
        'id' => (string) Str::uuid(),
        'env' => 'demo',
        'inst_id' => 'BTC-USDT-SWAP',
        'dataset' => 'candles:1H',
        'target_from' => '2024-10-29 00:00:00+00',
        'status' => 'pending',
        ...$overrides,
    ];
}

/**
 * @param  array<string, mixed>  $overrides
 * @return array<string, mixed>
 */
function qualityIssue(array $overrides = []): array
{
    return [
        'id' => (string) Str::uuid(),
        'env' => 'demo',
        'inst_id' => 'BTC-USDT-SWAP',
        'dataset' => 'candles:1m',
        'kind' => 'gap',
        'from_ts' => '2026-10-28 10:00:00+00',
        'to_ts' => '2026-10-28 10:05:00+00',
        'detail' => json_encode(['missing_bars' => 5]),
        'detected_at' => '2026-10-28 10:10:00+00',
        ...$overrides,
    ];
}

it('makes the five market-data tables TimescaleDB hypertables on ts', function (): void {
    $rows = collect(DB::select(
        'SELECT hypertable_name, column_name FROM timescaledb_information.dimensions WHERE hypertable_name = ANY (?)',
        ['{'.implode(',', MARKET_HYPERTABLES).'}'],
    ));

    expect($rows->pluck('hypertable_name')->sort()->values()->all())
        ->toEqual(collect(MARKET_HYPERTABLES)->sort()->values()->all());
    expect($rows->pluck('column_name')->unique()->values()->all())->toBe(['ts']);

    $names = collect(DB::select('SELECT hypertable_name FROM timescaledb_information.hypertables'))
        ->pluck('hypertable_name')->all();
    expect($names)->toContain(...MARKET_HYPERTABLES);
});

it('uses a one-day chunk interval for candles', function (): void {
    $interval = DB::scalar(
        "SELECT time_interval::text FROM timescaledb_information.dimensions WHERE hypertable_name = 'candles'"
    );

    expect($interval)->toBe('1 day');
});

it('stores every price, size and volume column as numeric, never double precision', function (): void {
    $moneyColumns = [
        'candles' => ['o', 'h', 'l', 'c', 'vol', 'vol_ccy', 'vol_ccy_quote'],
        'funding_rates' => ['funding_rate', 'realized_rate'],
        'open_interest' => ['oi', 'oi_ccy', 'oi_usd'],
        'long_short_ratio' => ['ratio'],
        'taker_volume' => ['buy_vol', 'sell_vol'],
    ];

    foreach ($moneyColumns as $table => $columns) {
        $types = marketColumnTypes($table);
        foreach ($columns as $column) {
            expect($types[$column] ?? null)->toBe('numeric', "{$table}.{$column}");
        }
    }

    $floats = DB::select(
        "SELECT table_name, column_name FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = ANY (?)
           AND data_type IN ('double precision', 'real')",
        ['{'.implode(',', [...MARKET_HYPERTABLES, 'backfill_jobs', 'data_quality_issues']).'}'],
    );
    expect($floats)->toBe([]);
});

it('keys candles on env, inst_id, bar and ts', function (): void {
    $row = [
        'env' => 'demo', 'inst_id' => 'BTC-USDT-SWAP', 'bar' => '1m', 'ts' => '2026-10-28 10:00:00+00',
        'o' => '1', 'h' => '2', 'l' => '0.5', 'c' => '1.5', 'vol' => '10', 'confirmed' => true,
    ];

    DB::table('candles')->insert($row);
    DB::table('candles')->insert([...$row, 'bar' => '5m']);
    DB::table('candles')->insert([...$row, 'env' => 'live']);

    expect(fn () => DB::table('candles')->insert($row))->toThrow(UniqueConstraintViolationException::class);
});

it('upserts a candle onto its natural key', function (): void {
    $row = [
        'env' => 'demo', 'inst_id' => 'XAU-USDT-SWAP', 'bar' => '1H', 'ts' => '2026-10-28 10:00:00+00',
        'o' => '4000.1', 'h' => '4010.2', 'l' => '3990.3', 'c' => '4005.4', 'vol' => '12.5', 'confirmed' => false,
    ];

    DB::table('candles')->upsert([$row], ['env', 'inst_id', 'bar', 'ts'], ['c', 'confirmed']);
    DB::table('candles')->upsert([[...$row, 'c' => '4007.123456789012345678', 'confirmed' => true]], ['env', 'inst_id', 'bar', 'ts'], ['c', 'confirmed']);

    expect(DB::table('candles')->where('inst_id', 'XAU-USDT-SWAP')->count())->toBe(1);
    $stored = DB::table('candles')->where('inst_id', 'XAU-USDT-SWAP')->sole();
    expect($stored->c)->toBe('4007.123456789012345678');
    expect($stored->confirmed)->toBeTrue();
});

it('refuses a candle bar outside 1m/5m/15m/1H/4H/1D', function (): void {
    expect(fn () => DB::table('candles')->insert([
        'env' => 'demo', 'inst_id' => 'BTC-USDT-SWAP', 'bar' => '2H', 'ts' => '2026-10-28 10:00:00+00',
        'o' => '1', 'h' => '1', 'l' => '1', 'c' => '1', 'vol' => '1',
    ]))->toThrow(QueryException::class);
});

it('creates backfill_jobs and data_quality_issues with the listed columns', function (): void {
    expect(array_keys(marketColumnTypes('backfill_jobs')))->toEqualCanonicalizing([
        'id', 'env', 'inst_id', 'dataset', 'target_from', 'cursor_ts', 'rows_written', 'status',
        'last_error', 'created_at', 'updated_at',
    ]);
    expect(array_keys(marketColumnTypes('data_quality_issues')))->toEqualCanonicalizing([
        'id', 'env', 'inst_id', 'dataset', 'kind', 'from_ts', 'to_ts', 'detail', 'detected_at',
        'repaired_at', 'created_at', 'updated_at',
    ]);
    expect(marketColumnTypes('data_quality_issues')['detail'])->toBe('jsonb');
});

it('accepts every backfill status and refuses any other', function (): void {
    foreach (['pending', 'running', 'done', 'failed', 'paused'] as $i => $status) {
        DB::table('backfill_jobs')->insert(backfillJob(['status' => $status, 'inst_id' => "I{$i}-USDT-SWAP"]));
    }
    expect(DB::table('backfill_jobs')->count())->toBe(5);
    expect(DB::table('backfill_jobs')->where('inst_id', 'I0-USDT-SWAP')->value('rows_written'))->toBe(0);

    expect(fn () => DB::table('backfill_jobs')->insert(backfillJob(['status' => 'cancelled'])))
        ->toThrow(QueryException::class, 'check constraint');
});

// A failed statement aborts the test's transaction, so each refusal is the last statement of its test.
it('refuses an unknown backfill dataset', function (): void {
    expect(fn () => DB::table('backfill_jobs')->insert(backfillJob(['dataset' => 'candles:2H'])))
        ->toThrow(QueryException::class, 'check constraint');
});

it('refuses a second backfill job for the same env, instrument and dataset', function (): void {
    DB::table('backfill_jobs')->insert(backfillJob(['dataset' => 'funding']));
    expect(fn () => DB::table('backfill_jobs')->insert(backfillJob(['dataset' => 'funding'])))
        ->toThrow(UniqueConstraintViolationException::class);
});

it('accepts every data-quality kind and refuses any other', function (): void {
    foreach (['gap', 'duplicate', 'zero_volume', 'outlier'] as $kind) {
        DB::table('data_quality_issues')->insert(qualityIssue(['kind' => $kind]));
    }
    expect(DB::table('data_quality_issues')->count())->toBe(4);

    expect(fn () => DB::table('data_quality_issues')->insert(qualityIssue(['kind' => 'spike'])))
        ->toThrow(QueryException::class, 'check constraint');
});
