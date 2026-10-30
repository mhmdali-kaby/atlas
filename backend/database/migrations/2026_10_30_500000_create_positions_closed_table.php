<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Portfolio history (brief §13 History, decision #236, ATL-172).
 *
 * positions_closed: one row per OKX `account/positions-history` record (a full or partial close,
 * a liquidation or an ADL), upserted on (env, pos_id, closed_at). proposal_id / agent are filled by
 * trading_core when the position was opened by an Atlas entry order; otherwise origin = external.
 * R is not stored: the API derives it from the linked proposal's stop, so it always follows the
 * proposal row.
 *
 * fills.fill_pnl: OKX `fillPnl` (realised PnL of a closing fill, 0 on opening fills), so History can
 * filter fills by win/loss.
 */
return new class extends Migration
{
    public const ENVS = ['demo', 'live'];

    public const SIDES = ['long', 'short'];

    public const POS_SIDES = ['net', 'long', 'short'];

    public const ORIGINS = ['atlas', 'external'];

    public function up(): void
    {
        Schema::create('positions_closed', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->string('pos_id', 64);
            $table->string('inst_id', 64);
            $table->string('inst_type', 16);
            $table->string('mgn_mode', 16);
            $table->enum('pos_side', self::POS_SIDES);
            // Direction of the position that was closed.
            $table->enum('side', self::SIDES);
            // OKX type: 1 partial close, 2 close all, 3 liquidation, 4 partial liquidation, 5 ADL…
            $table->string('close_type', 8);
            $table->decimal('lever', 38, 18)->nullable();
            $table->string('ccy', 32)->nullable();
            // Contracts closed (OKX closeTotalPos).
            $table->decimal('size', 38, 18);
            $table->decimal('entry_px', 38, 18)->nullable();
            $table->decimal('exit_px', 38, 18)->nullable();
            // OKX realizedPnl = pnl + fee + funding_fee + liq_penalty, in ccy.
            $table->decimal('realised_pnl', 38, 18)->nullable();
            $table->decimal('pnl', 38, 18)->nullable();
            // OKX sign: negative = paid.
            $table->decimal('fee', 38, 18)->nullable();
            $table->decimal('funding_fee', 38, 18)->nullable();
            $table->decimal('liq_penalty', 38, 18)->nullable();
            $table->decimal('pnl_ratio', 38, 18)->nullable();
            $table->timestampTz('opened_at', 6)->nullable();
            $table->timestampTz('closed_at', 6);
            $table->foreignUuid('proposal_id')->nullable()->constrained('proposals')->nullOnDelete();
            // Proposal author (agent slug) when linked.
            $table->string('agent', 64)->nullable();
            // Setup / strategy tag (e.g. breakout); null until the proposal carries one.
            $table->string('setup_tag', 64)->nullable();
            $table->enum('origin', self::ORIGINS)->default('external');
            $table->jsonb('raw')->default('{}');
            $table->timestampsTz(6);

            $table->unique(['env', 'pos_id', 'closed_at']);
            $table->index(['env', 'closed_at']);
            $table->index(['env', 'inst_id']);
            $table->index('proposal_id');
        });

        Schema::table('fills', function (Blueprint $table): void {
            $table->decimal('fill_pnl', 38, 18)->nullable();
        });

        DB::statement("ALTER TABLE positions_closed ADD CONSTRAINT positions_closed_atlas_has_proposal CHECK (origin <> 'atlas' OR proposal_id IS NOT NULL)");
    }

    public function down(): void
    {
        Schema::table('fills', function (Blueprint $table): void {
            $table->dropColumn('fill_pnl');
        });
        Schema::dropIfExists('positions_closed');
    }
};
