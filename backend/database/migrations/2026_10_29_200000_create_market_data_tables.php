<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Market history (brief §5, §7.3): five TimescaleDB hypertables on ts plus the backfill-progress
 * and data-quality tables.
 *
 * The hypertables carry no surrogate id: their primary key is the natural key (it contains ts, as
 * Timescale requires of every unique index), so Python writes them with
 * `INSERT … ON CONFLICT (<primary key>) DO UPDATE`. Prices, sizes and volumes are numeric(38,18).
 */
return new class extends Migration
{
    private const ENVS = ['demo', 'live'];

    private const BARS = ['1m', '5m', '15m', '1H', '4H', '1D'];

    /** OKX rubik `period` values. */
    private const PERIODS = ['5m', '1H', '1D'];

    private const DATASETS = [
        'candles:1m', 'candles:5m', 'candles:15m', 'candles:1H', 'candles:4H', 'candles:1D',
        'funding', 'open_interest', 'long_short_ratio', 'taker_volume',
    ];

    /** Chunk interval per hypertable: 1m candles are the big one (~8M rows a month). */
    private const CHUNKS = [
        'candles' => '1 day',
        'funding_rates' => '30 days',
        'open_interest' => '7 days',
        'long_short_ratio' => '30 days',
        'taker_volume' => '30 days',
    ];

    public function up(): void
    {
        Schema::create('candles', function (Blueprint $table): void {
            $table->enum('env', self::ENVS);
            $table->string('inst_id', 64);
            $table->enum('bar', self::BARS);
            $table->timestampTz('ts', 6);
            $table->decimal('o', 38, 18);
            $table->decimal('h', 38, 18);
            $table->decimal('l', 38, 18);
            $table->decimal('c', 38, 18);
            $table->decimal('vol', 38, 18);
            $table->decimal('vol_ccy', 38, 18)->nullable();
            $table->decimal('vol_ccy_quote', 38, 18)->nullable();
            $table->boolean('confirmed')->default(false);

            $table->primary(['env', 'inst_id', 'bar', 'ts']);
        });

        Schema::create('funding_rates', function (Blueprint $table): void {
            $table->enum('env', self::ENVS);
            $table->string('inst_id', 64);
            $table->timestampTz('ts', 6);
            $table->decimal('funding_rate', 38, 18);
            $table->decimal('realized_rate', 38, 18)->nullable();

            $table->primary(['env', 'inst_id', 'ts']);
        });

        // inst_id holds an OKX instId (public/open-interest) or a bare ccy such as BTC for the
        // currency-level rubik series; the two never collide because instIds always contain '-'.
        Schema::create('open_interest', function (Blueprint $table): void {
            $table->enum('env', self::ENVS);
            $table->string('inst_id', 64);
            $table->timestampTz('ts', 6);
            $table->decimal('oi', 38, 18)->nullable();
            $table->decimal('oi_ccy', 38, 18)->nullable();
            $table->decimal('oi_usd', 38, 18)->nullable();

            $table->primary(['env', 'inst_id', 'ts']);
        });

        Schema::create('long_short_ratio', function (Blueprint $table): void {
            $table->enum('env', self::ENVS);
            $table->string('ccy', 32);
            $table->enum('period', self::PERIODS);
            $table->timestampTz('ts', 6);
            $table->decimal('ratio', 38, 18);

            $table->primary(['env', 'ccy', 'period', 'ts']);
        });

        Schema::create('taker_volume', function (Blueprint $table): void {
            $table->enum('env', self::ENVS);
            $table->string('ccy', 32);
            $table->enum('period', self::PERIODS);
            $table->timestampTz('ts', 6);
            $table->decimal('buy_vol', 38, 18);
            $table->decimal('sell_vol', 38, 18);

            $table->primary(['env', 'ccy', 'period', 'ts']);
        });

        Schema::create('backfill_jobs', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->string('inst_id', 64);
            $table->enum('dataset', self::DATASETS);
            // Oldest ts the job wants; the backfill walks backwards from now until cursor_ts <= target_from.
            $table->timestampTz('target_from', 6);
            $table->timestampTz('cursor_ts', 6)->nullable();
            $table->unsignedBigInteger('rows_written')->default(0);
            $table->enum('status', ['pending', 'running', 'done', 'failed', 'paused'])->default('pending');
            $table->text('last_error')->nullable();
            $table->timestampsTz(6);

            $table->unique(['env', 'inst_id', 'dataset']);
            $table->index(['env', 'status']);
        });

        Schema::create('data_quality_issues', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->string('inst_id', 64);
            $table->enum('dataset', self::DATASETS);
            $table->enum('kind', ['gap', 'duplicate', 'zero_volume', 'outlier']);
            $table->timestampTz('from_ts', 6);
            $table->timestampTz('to_ts', 6);
            $table->jsonb('detail')->nullable();
            $table->timestampTz('detected_at', 6);
            $table->timestampTz('repaired_at', 6)->nullable();
            $table->timestampsTz(6);

            // Re-running a check upserts the same issue instead of adding a copy.
            $table->unique(['env', 'inst_id', 'dataset', 'kind', 'from_ts']);
            $table->index(['env', 'inst_id', 'dataset', 'repaired_at']);
        });

        if (DB::connection()->getDriverName() !== 'pgsql') {
            return;
        }

        DB::statement('CREATE EXTENSION IF NOT EXISTS timescaledb');
        foreach (self::CHUNKS as $table => $interval) {
            DB::statement(
                "SELECT create_hypertable('{$table}', 'ts', chunk_time_interval => INTERVAL '{$interval}', if_not_exists => TRUE)"
            );
        }
    }

    public function down(): void
    {
        Schema::dropIfExists('data_quality_issues');
        Schema::dropIfExists('backfill_jobs');
        foreach (array_reverse(array_keys(self::CHUNKS)) as $table) {
            Schema::dropIfExists($table);
        }
    }
};
