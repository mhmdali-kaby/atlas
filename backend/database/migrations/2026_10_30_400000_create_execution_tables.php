<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Executor tables (brief §5, §11; decision #249 patch G, ATL-141): orders, algo_orders, fills,
 * and trading_state (paused flag + circuit breakers) unless ATL-127 already created it.
 *
 * Prices, sizes and fees are numeric(38,18) (Decimal in Python). Every state/kind/origin list is a
 * CHECK, so a bad value fails at insert. clOrdId is unique across the table: retries reuse it (rule 8)
 * and a second insert with the same id is refused by the database.
 */
return new class extends Migration
{
    public const ENVS = ['demo', 'live'];

    /** The Executor's order state machine. */
    public const ORDER_STATES = ['new', 'sending', 'live', 'partially_filled', 'filled', 'canceled', 'rejected', 'failed'];

    /** atlas = placed by the Executor; external = found on OKX by reconciliation (brief §4.4). */
    public const ORIGINS = ['atlas', 'external'];

    public const PURPOSES = ['entry', 'protect', 'close', 'kill'];

    public const SIDES = ['buy', 'sell'];

    /** OKX posSide; `net` in one-way (net) position mode. */
    public const POS_SIDES = ['net', 'long', 'short'];

    /** OKX tdMode. */
    public const TD_MODES = ['isolated', 'cross', 'cash', 'spot_isolated'];

    /** OKX ordType values the Executor may send or reconciliation may import. */
    public const ORD_TYPES = ['market', 'limit', 'post_only', 'fok', 'ioc', 'optimal_limit_ioc', 'mmp', 'mmp_and_post_only', 'op_fok', 'elp'];

    public const ALGO_KINDS = ['sl', 'tp', 'oco', 'trigger'];

    /** Our pre-send states (new, sending, failed) plus OKX's algo order states. */
    public const ALGO_STATES = ['new', 'sending', 'live', 'pause', 'effective', 'partially_effective', 'canceled', 'order_failed', 'partially_failed', 'failed'];

    public function up(): void
    {
        Schema::create('orders', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->foreignUuid('proposal_id')->nullable()->constrained('proposals')->nullOnDelete();
            $table->foreignUuid('risk_check_id')->nullable()->constrained('risk_checks')->nullOnDelete();
            // Ours: 1-32 alphanumerics starting with a letter (OKX rule). Null only for external
            // orders placed without one.
            $table->string('cl_ord_id', 32)->nullable()->unique();
            $table->string('ord_id', 64)->nullable();
            $table->string('inst_id', 64);
            $table->enum('side', self::SIDES);
            $table->enum('pos_side', self::POS_SIDES)->nullable();
            $table->enum('ord_type', self::ORD_TYPES);
            $table->enum('td_mode', self::TD_MODES);
            $table->decimal('px', 38, 18)->nullable();
            $table->decimal('sz', 38, 18);
            $table->decimal('filled_sz', 38, 18)->default(0);
            $table->decimal('avg_px', 38, 18)->nullable();
            $table->integer('lever')->nullable();
            $table->decimal('fee', 38, 18)->nullable();
            $table->string('fee_ccy', 32)->nullable();
            $table->enum('state', self::ORDER_STATES)->default('new');
            $table->boolean('reduce_only')->default(false);
            $table->enum('origin', self::ORIGINS)->default('atlas');
            $table->enum('purpose', self::PURPOSES)->default('entry');
            $table->string('last_error_code', 16)->nullable();
            $table->text('last_error_msg')->nullable();
            // Last OKX order payload seen (REST or WS), for audit and reconciliation.
            $table->jsonb('raw')->default('{}');
            $table->timestampsTz(6);

            $table->unique(['env', 'ord_id']);
            $table->index(['env', 'state']);
            $table->index(['env', 'inst_id', 'created_at']);
            $table->index('proposal_id');
        });

        Schema::create('algo_orders', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            // The entry order a TP/SL protects (attached or placed after it); null for external.
            $table->foreignUuid('order_id')->nullable()->constrained('orders')->nullOnDelete();
            $table->string('algo_id', 64)->nullable();
            $table->string('algo_cl_ord_id', 32)->nullable()->unique();
            $table->string('inst_id', 64);
            $table->enum('kind', self::ALGO_KINDS);
            $table->enum('side', self::SIDES)->nullable();
            $table->enum('pos_side', self::POS_SIDES)->nullable();
            // sl / tp / trigger use trigger_px; an oco carries both sl_ and tp_trigger_px.
            $table->decimal('trigger_px', 38, 18)->nullable();
            $table->decimal('sl_trigger_px', 38, 18)->nullable();
            $table->decimal('tp_trigger_px', 38, 18)->nullable();
            // Execution price once triggered; null = market (OKX "-1").
            $table->decimal('ord_px', 38, 18)->nullable();
            // Null = the whole position (OKX closeFraction 1).
            $table->decimal('sz', 38, 18)->nullable();
            $table->enum('state', self::ALGO_STATES)->default('new');
            $table->boolean('reduce_only')->default(true);
            $table->enum('origin', self::ORIGINS)->default('atlas');
            $table->string('last_error_code', 16)->nullable();
            $table->text('last_error_msg')->nullable();
            $table->jsonb('raw')->default('{}');
            $table->timestampsTz(6);

            $table->unique(['env', 'algo_id']);
            $table->index(['env', 'state']);
            $table->index(['env', 'inst_id']);
            $table->index('order_id');
        });

        Schema::create('fills', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->foreignUuid('order_id')->nullable()->constrained('orders')->nullOnDelete();
            $table->string('trade_id', 64);
            $table->string('ord_id', 64);
            $table->string('cl_ord_id', 32)->nullable();
            $table->string('bill_id', 64)->nullable();
            $table->string('inst_id', 64);
            $table->enum('side', self::SIDES);
            $table->enum('pos_side', self::POS_SIDES)->nullable();
            $table->decimal('fill_px', 38, 18);
            $table->decimal('fill_sz', 38, 18);
            // OKX sign convention: negative = fee paid, positive = rebate.
            $table->decimal('fee', 38, 18)->nullable();
            $table->string('fee_ccy', 32)->nullable();
            // OKX execType: T taker, M maker.
            $table->string('exec_type', 4)->nullable();
            $table->timestampTz('ts', 6);
            $table->jsonb('raw')->default('{}');
            $table->timestampsTz(6);

            // OKX tradeId is unique per instrument.
            $table->unique(['env', 'inst_id', 'trade_id']);
            $table->index(['env', 'ts']);
            $table->index(['env', 'ord_id']);
        });

        DB::statement('ALTER TABLE orders ADD CONSTRAINT orders_sz_positive CHECK (sz > 0)');
        DB::statement('ALTER TABLE orders ADD CONSTRAINT orders_filled_sz_range CHECK (filled_sz >= 0 AND filled_sz <= sz)');
        DB::statement('ALTER TABLE orders ADD CONSTRAINT orders_lever_positive CHECK (lever >= 1)');
        // Rule 8: every order Atlas places carries a clOrdId.
        DB::statement("ALTER TABLE orders ADD CONSTRAINT orders_atlas_has_cl_ord_id CHECK (origin <> 'atlas' OR cl_ord_id IS NOT NULL)");
        // Rule 1: an entry Atlas places exists only for a proposal that passed a risk check.
        DB::statement("ALTER TABLE orders ADD CONSTRAINT orders_atlas_entry_risk_checked CHECK (origin <> 'atlas' OR purpose <> 'entry' OR (proposal_id IS NOT NULL AND risk_check_id IS NOT NULL))");
        DB::statement("ALTER TABLE algo_orders ADD CONSTRAINT algo_orders_atlas_has_cl_ord_id CHECK (origin <> 'atlas' OR algo_cl_ord_id IS NOT NULL)");
        DB::statement('ALTER TABLE algo_orders ADD CONSTRAINT algo_orders_sz_positive CHECK (sz > 0)');
        DB::statement("ALTER TABLE algo_orders ADD CONSTRAINT algo_orders_oco_has_both CHECK (kind <> 'oco' OR (sl_trigger_px IS NOT NULL AND tp_trigger_px IS NOT NULL))");
        DB::statement("ALTER TABLE algo_orders ADD CONSTRAINT algo_orders_single_has_trigger CHECK (kind = 'oco' OR trigger_px IS NOT NULL)");
        DB::statement('ALTER TABLE fills ADD CONSTRAINT fills_sz_positive CHECK (fill_sz > 0)');

        // ATL-127 owns circuit breakers and may have created this first; same shape either way.
        if (! Schema::hasTable('trading_state')) {
            Schema::create('trading_state', function (Blueprint $table): void {
                $table->uuid('id')->primary();
                $table->enum('env', self::ENVS)->unique();
                $table->boolean('paused')->default(false);
                $table->text('reason')->nullable();
                // breaker name => {"active": bool, "since": ISO-8601, "detail": "…"}.
                $table->jsonb('breakers')->default('{}');
                $table->timestampTz('paused_at', 6)->nullable();
                $table->timestampsTz(6);
            });
        }
    }

    public function down(): void
    {
        Schema::dropIfExists('fills');
        Schema::dropIfExists('algo_orders');
        Schema::dropIfExists('orders');
        // trading_state is dropped only by whichever migration created it; leaving it is harmless.
    }
};
