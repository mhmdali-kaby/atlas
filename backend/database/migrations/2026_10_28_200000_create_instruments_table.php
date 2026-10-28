<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * OKX instrument universe per environment (brief §4.3).
 * Every size and price is numeric(38,18); never float.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('instruments', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', ['demo', 'live']);
            $table->string('inst_id', 64);
            $table->string('inst_type', 16);
            $table->string('inst_family', 64)->nullable();
            $table->string('inst_category', 16)->nullable();
            $table->string('base_ccy', 32)->nullable();
            $table->string('quote_ccy', 32)->nullable();
            $table->string('settle_ccy', 32)->nullable();
            $table->decimal('ct_val', 38, 18)->nullable();
            $table->string('ct_val_ccy', 32)->nullable();
            $table->decimal('ct_mult', 38, 18)->nullable();
            $table->decimal('lot_sz', 38, 18);
            $table->decimal('min_sz', 38, 18);
            $table->decimal('tick_sz', 38, 18);
            $table->decimal('max_lever', 38, 18)->nullable();
            $table->string('state', 16);
            $table->timestampTz('list_time', 6)->nullable();
            $table->timestampTz('exp_time', 6)->nullable();
            $table->enum('asset_class', ['crypto', 'stock', 'etf', 'index', 'commodity', 'other'])->default('other');
            $table->timestampTz('last_seen_at', 6)->nullable();
            $table->timestampsTz(6);

            $table->unique(['env', 'inst_id']);
            $table->index(['env', 'inst_type', 'state']);
            $table->index(['env', 'asset_class']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('instruments');
    }
};
