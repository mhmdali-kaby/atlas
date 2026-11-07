<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Owner backtest runs (decision #253 L, ATL-208). POST /api/backtests writes a queued row; the
 * RunBacktest job calls trading_core's signed POST /internal/backtest and stores its body in `result`
 * (status done) or trading_core's error code and reason (status failed). Read-only research: no row
 * here is ever an order.
 */
return new class extends Migration
{
    public const ENVS = ['demo', 'live'];

    public const STATUSES = ['queued', 'running', 'done', 'failed'];

    public function up(): void
    {
        Schema::create('backtest_runs', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->foreignUuid('user_id')->nullable()->constrained('users')->nullOnDelete();
            // The validated body exactly as sent to trading_core.
            $table->jsonb('request');
            $table->enum('status', self::STATUSES)->default('queued');
            // trading_core's 200 body, unchanged.
            $table->jsonb('result')->nullable();
            // Failure code (trading_core's 422 `error`, or trading_core_error / trading_core_unavailable / timeout).
            $table->string('error', 64)->nullable();
            // One-line human reason for a failure, from trading_core where it gave one.
            $table->text('error_reason')->nullable();
            $table->timestampTz('started_at', 6)->nullable();
            $table->timestampTz('finished_at', 6)->nullable();
            $table->timestampsTz(6);

            $table->index(['env', 'user_id', 'created_at']);
        });

        DB::statement("ALTER TABLE backtest_runs ADD CONSTRAINT backtest_runs_failed_has_error CHECK (status <> 'failed' OR error IS NOT NULL)");
    }

    public function down(): void
    {
        Schema::dropIfExists('backtest_runs');
    }
};
