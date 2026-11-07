<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * ATL-236, decision #255 (4): the single system_state row. active_env is the env trading_core and
 * the agents run for; live_started_at is set by the first switch to live (the first live month counts
 * from it); preflight_attestations holds the owner's three ticked items
 * ({"ip_allowlist": {"confirmed_at": "…"}, …}). No row is inserted here: ActiveEnv falls back to
 * config('atlas_defaults.env.default') until the first switch or attestation writes it.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('system_state', function (Blueprint $table): void {
            $table->smallInteger('id')->primary();
            $table->enum('active_env', ['demo', 'live'])->default('demo');
            $table->timestampTz('live_started_at', 6)->nullable();
            $table->timestampTz('switched_at', 6)->nullable();
            $table->jsonb('preflight_attestations')->default('{}');
            $table->timestampsTz(6);
        });

        // Exactly one row, ever.
        DB::statement('ALTER TABLE system_state ADD CONSTRAINT system_state_single_row CHECK (id = 1)');
    }

    public function down(): void
    {
        Schema::dropIfExists('system_state');
    }
};
