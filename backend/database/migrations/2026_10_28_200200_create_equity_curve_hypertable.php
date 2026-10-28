<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Equity curve, one row per env per minute, as a TimescaleDB hypertable on ts.
 * The primary key includes ts because Timescale requires every unique index to contain
 * the partitioning column. On non-pgsql drivers the hypertable step is skipped.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('equity_curve', function (Blueprint $table): void {
            $table->uuid('id');
            $table->enum('env', ['demo', 'live']);
            $table->timestampTz('ts', 6);
            $table->decimal('total_eq_usd', 38, 18);
            $table->decimal('upl_usd', 38, 18)->nullable();
            $table->timestampsTz(6);

            $table->primary(['id', 'ts']);
            $table->unique(['env', 'ts']);
        });

        if (DB::connection()->getDriverName() !== 'pgsql') {
            return;
        }

        DB::statement('CREATE EXTENSION IF NOT EXISTS timescaledb');
        DB::statement("SELECT create_hypertable('equity_curve', 'ts', if_not_exists => TRUE)");
    }

    public function down(): void
    {
        Schema::dropIfExists('equity_curve');
    }
};
