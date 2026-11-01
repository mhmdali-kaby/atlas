<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Paper trades (brief §7.6 Scoring "regret of skipped ideas", §7.7 Scoring, ATL-196).
 *
 * One row per entry proposal that was never executed (vote rejected, risk rejected, owner rejected,
 * expired, advise-only), simulated by code (atlas_agents.scorecards.paper) from the latest version on
 * stored candles. proposal_id is that version's id and is unique: re-running the job updates the row.
 * status filled = the simulated entry filled and the trade has exited; not_filled = a limit entry never
 * traded through within the entry timeout (realised R null: it is not a loss).
 *
 * r_gross is the price result in R of the per-unit risk |entry - stop|; fees_r is the simulated taker
 * fees (entry + exits) in the same unit; realised_r = r_gross - fees_r.
 */
return new class extends Migration
{
    public const ENVS = ['demo', 'live'];

    public const STATUSES = ['filled', 'not_filled'];

    public const EXIT_REASONS = ['stop', 'take_profit', 'time_stop', 'horizon', 'not_filled'];

    public function up(): void
    {
        Schema::create('paper_trades', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->foreignUuid('proposal_id')->unique()->constrained('proposals')->cascadeOnDelete();
            $table->unsignedInteger('version')->default(1);
            $table->string('inst_id', 64);
            $table->string('side', 8);
            $table->string('order_type', 16);
            // Why the proposal was not executed: its terminal status (rejected, expired, advise_only)
            // and status_reason (vote_rejected, risk_rejected, owner_rejected, approval_timeout, ...).
            $table->string('proposal_status', 32);
            $table->string('proposal_status_reason', 64)->nullable();
            $table->enum('status', self::STATUSES);
            // When the idea would have been sent: the latest version's created_at.
            $table->timestampTz('decided_at', 6);
            $table->decimal('entry_px', 38, 18)->nullable();
            $table->timestampTz('entry_ts', 6)->nullable();
            $table->decimal('stop', 38, 18);
            // Size-weighted average exit price.
            $table->decimal('exit_px', 38, 18)->nullable();
            $table->timestampTz('exit_ts', 6)->nullable();
            $table->enum('exit_reason', self::EXIT_REASONS);
            $table->decimal('realised_r', 20, 8)->nullable();
            $table->decimal('r_gross', 20, 8)->nullable();
            $table->decimal('fees_r', 20, 8)->nullable();
            $table->decimal('fee_rate', 12, 8)->nullable();
            // Whether TP1 was reached before the stop (null when not filled).
            $table->boolean('tp1_hit')->nullable();
            // Candle bar walked: 1m, or 15m when 1m was missing.
            $table->string('bar', 8)->nullable();
            // Legs: [{reason, price, ts, pct, r}] in order.
            $table->jsonb('legs')->default('[]');
            $table->timestampTz('simulated_at', 6);
            $table->timestampsTz(6);

            $table->index(['env', 'decided_at']);
        });

        DB::statement(
            'ALTER TABLE paper_trades ADD CONSTRAINT paper_trades_filled_has_r CHECK ('.
            "(status = 'filled' AND realised_r IS NOT NULL AND entry_px IS NOT NULL AND exit_ts IS NOT NULL) ".
            "OR (status = 'not_filled' AND realised_r IS NULL AND exit_reason = 'not_filled'))"
        );
    }

    public function down(): void
    {
        Schema::dropIfExists('paper_trades');
    }
};
