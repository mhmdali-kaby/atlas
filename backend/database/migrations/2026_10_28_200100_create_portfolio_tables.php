<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * Account data synced from OKX by trading_core (decision #235).
 * Amounts are numeric(38,18) in the row's own ccy unless the column name ends in _usd.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('balances_snapshots', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', ['demo', 'live']);
            $table->string('ccy', 32);
            $table->enum('account', ['trading', 'funding']);
            $table->decimal('eq', 38, 18);
            $table->decimal('avail_bal', 38, 18)->nullable();
            $table->decimal('frozen_bal', 38, 18)->nullable();
            $table->decimal('eq_usd', 38, 18)->nullable();
            $table->decimal('upl', 38, 18)->nullable();
            $table->timestampTz('taken_at', 6);
            $table->timestampsTz(6);

            $table->unique(['env', 'account', 'ccy', 'taken_at']);
            $table->index(['env', 'taken_at']);
        });

        Schema::create('positions_snapshots', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', ['demo', 'live']);
            $table->string('inst_id', 64);
            $table->string('pos_side', 8);
            $table->decimal('pos', 38, 18);
            $table->decimal('avg_px', 38, 18)->nullable();
            $table->decimal('mark_px', 38, 18)->nullable();
            $table->decimal('liq_px', 38, 18)->nullable();
            $table->decimal('lever', 38, 18)->nullable();
            $table->decimal('upl', 38, 18)->nullable();
            $table->decimal('upl_ratio', 38, 18)->nullable();
            $table->decimal('margin', 38, 18)->nullable();
            $table->string('mgn_mode', 16)->nullable();
            $table->timestampTz('c_time', 6)->nullable();
            $table->timestampTz('taken_at', 6);
            $table->timestampsTz(6);

            $table->unique(['env', 'inst_id', 'pos_side', 'taken_at']);
            $table->index(['env', 'taken_at']);
        });

        Schema::create('bills', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', ['demo', 'live']);
            $table->string('bill_id', 64);
            $table->string('inst_id', 64)->nullable();
            $table->string('ccy', 32);
            $table->string('type', 16);
            $table->string('sub_type', 16)->nullable();
            $table->decimal('bal_chg', 38, 18);
            $table->decimal('pnl', 38, 18)->nullable();
            $table->decimal('fee', 38, 18)->nullable();
            $table->timestampTz('ts', 6);
            $table->timestampsTz(6);

            $table->unique(['env', 'bill_id']);
            $table->index(['env', 'ts']);
        });

        Schema::create('deposits_withdrawals', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', ['demo', 'live']);
            $table->enum('kind', ['deposit', 'withdrawal']);
            $table->string('external_id', 64);
            $table->string('ccy', 32);
            $table->decimal('amount', 38, 18);
            $table->string('state', 16);
            $table->string('tx_id', 128)->nullable();
            $table->timestampTz('ts', 6);
            $table->timestampsTz(6);

            $table->unique(['env', 'kind', 'external_id']);
            $table->index(['env', 'ts']);
        });

        Schema::create('sync_status', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', ['demo', 'live']);
            $table->string('stream', 32);
            $table->timestampTz('last_success_at', 6)->nullable();
            $table->text('last_error')->nullable();
            $table->timestampTz('reconciled_at', 6)->nullable();
            $table->timestampsTz(6);

            $table->unique(['env', 'stream']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('sync_status');
        Schema::dropIfExists('deposits_withdrawals');
        Schema::dropIfExists('bills');
        Schema::dropIfExists('positions_snapshots');
        Schema::dropIfExists('balances_snapshots');
    }
};
