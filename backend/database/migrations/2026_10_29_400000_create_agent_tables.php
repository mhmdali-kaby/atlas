<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Agent runtime tables for patch D (decision #245, brief §5, §6, §7.11): runs, daily usage,
 * live states, versioned prompts, research reports, the shared notebook, the journal and scores.
 *
 * `agent` is the slug from config/defaults.yaml warroom.agents (e.g. market_scout). It is a plain
 * string, validated by the runtime, so adding an agent never needs a migration.
 * Every jsonb column is NOT NULL with a '{}' default, so jsonb_set on it never yields NULL.
 */
return new class extends Migration
{
    public const ENVS = ['demo', 'live'];

    public const RUN_TRIGGERS = ['mention', 'schedule', 'eval', 'manual'];

    public const RUN_STATUSES = ['running', 'ok', 'error', 'degraded', 'rate_limited', 'over_budget'];

    public const AGENT_STATES = ['idle', 'thinking', 'waiting', 'degraded', 'rate_limited', 'paused', 'disabled'];

    public function up(): void
    {
        Schema::create('prompt_versions', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->string('agent', 64);
            $table->integer('version');
            $table->char('sha256', 64);
            $table->text('body');
            $table->timestampsTz(6);

            $table->unique(['agent', 'version']);
            $table->unique(['agent', 'sha256']);
        });

        Schema::create('agent_runs', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->string('agent', 64);
            $table->enum('trigger', self::RUN_TRIGGERS);
            $table->string('correlation_id', 191)->nullable();
            $table->timestampTz('started_at', 6);
            $table->timestampTz('ended_at', 6)->nullable();
            $table->enum('status', self::RUN_STATUSES)->default('running');
            $table->string('model', 100)->nullable();
            $table->integer('prompt_version')->nullable();
            $table->bigInteger('tokens_in')->default(0);
            $table->bigInteger('tokens_out')->default(0);
            $table->integer('turns')->default(0);
            // The SDK's cost figure; notional only, the subscription is what is billed (#242).
            $table->decimal('cost_usd_notional', 18, 6)->default(0);
            $table->text('error')->nullable();
            $table->timestampsTz(6);

            $table->index(['agent', 'started_at']);
            $table->index(['env', 'started_at']);
            $table->index('correlation_id');
        });

        Schema::create('agent_usage_daily', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->string('agent', 64);
            $table->enum('env', self::ENVS);
            // Asia/Baghdad calendar date the usage counts against.
            $table->date('day');
            $table->bigInteger('tokens_in')->default(0);
            $table->bigInteger('tokens_out')->default(0);
            $table->integer('turns')->default(0);
            $table->integer('runs')->default(0);
            $table->timestampsTz(6);

            $table->unique(['agent', 'env', 'day']);
        });

        Schema::create('agent_states', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->string('agent', 64);
            $table->enum('env', self::ENVS);
            $table->enum('state', self::AGENT_STATES)->default('idle');
            $table->text('current_task')->nullable();
            $table->timestampTz('state_since', 6)->useCurrent();
            $table->text('last_error')->nullable();
            $table->timestampsTz(6);

            $table->unique(['agent', 'env']);
        });

        Schema::create('research_reports', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->string('agent', 64);
            $table->foreignUuid('agent_run_id')->nullable()->constrained('agent_runs')->nullOnDelete();
            $table->string('inst_id', 64)->nullable();
            $table->string('kind', 64);
            $table->text('content_md');
            $table->jsonb('data')->default('{}');
            $table->decimal('confidence', 6, 4)->nullable();
            $table->timestampTz('valid_until', 6)->nullable();
            $table->timestampsTz(6);

            $table->index(['env', 'inst_id', 'created_at']);
            $table->index(['agent', 'created_at']);
        });

        Schema::create('agent_notebook', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            // Asia/Baghdad calendar date of the notebook page.
            $table->date('day');
            $table->string('agent', 64);
            $table->text('note_md');
            $table->jsonb('data')->default('{}');
            $table->timestampsTz(6);

            $table->index(['env', 'day', 'created_at']);
        });

        Schema::create('journal_entries', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->string('agent', 64);
            $table->string('inst_id', 64)->nullable();
            // The proposals table arrives in Phase 2; no foreign key until then.
            $table->uuid('proposal_id')->nullable();
            $table->string('title', 255);
            $table->text('expected_md')->nullable();
            $table->text('actual_md')->nullable();
            $table->text('lessons_md')->nullable();
            $table->jsonb('data')->default('{}');
            $table->timestampsTz(6);

            $table->index(['env', 'inst_id', 'created_at']);
        });

        Schema::create('agent_scores', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->string('agent', 64);
            $table->enum('env', self::ENVS);
            // Asia/Baghdad calendar date the score covers.
            $table->date('day');
            $table->integer('n')->default(0);
            $table->decimal('hit_rate', 6, 4)->nullable();
            $table->decimal('brier_score', 8, 6)->nullable();
            $table->jsonb('calibration')->default('{}');
            $table->decimal('pnl_attributed_usd', 38, 18)->nullable();
            $table->jsonb('data')->default('{}');
            $table->timestampsTz(6);

            $table->unique(['agent', 'env', 'day']);
        });

        // Blueprint has no text[] type; CHECKs keep ratios inside their ranges.
        DB::statement("ALTER TABLE journal_entries ADD COLUMN tags text[] NOT NULL DEFAULT '{}'");
        DB::statement('CREATE INDEX journal_entries_tags_gin ON journal_entries USING gin (tags)');
        DB::statement('ALTER TABLE research_reports ADD CONSTRAINT research_reports_confidence_range CHECK (confidence BETWEEN 0 AND 1)');
        DB::statement('ALTER TABLE agent_scores ADD CONSTRAINT agent_scores_hit_rate_range CHECK (hit_rate BETWEEN 0 AND 1)');
        DB::statement('ALTER TABLE agent_scores ADD CONSTRAINT agent_scores_brier_range CHECK (brier_score BETWEEN 0 AND 1)');
        DB::statement('ALTER TABLE prompt_versions ADD CONSTRAINT prompt_versions_version_positive CHECK (version >= 1)');
    }

    public function down(): void
    {
        Schema::dropIfExists('agent_scores');
        Schema::dropIfExists('journal_entries');
        Schema::dropIfExists('agent_notebook');
        Schema::dropIfExists('research_reports');
        Schema::dropIfExists('agent_states');
        Schema::dropIfExists('agent_usage_daily');
        Schema::dropIfExists('agent_runs');
        Schema::dropIfExists('prompt_versions');
    }
};
