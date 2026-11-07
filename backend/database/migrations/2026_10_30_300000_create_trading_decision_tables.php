<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Phase 2 decision tables (brief §5, §7.9, §8, §10; decision #249 patch F, ATL-124):
 * cycles, proposals, votes, risk_checks.
 *
 * Money, prices and sizes are numeric(38,18) (Decimal in Python, brick/math in PHP). Probabilities
 * and confidences are numeric(6,4), CHECKed to 0..1. Every status/state/result list is a CHECK
 * constraint, so a value outside it fails at the database.
 *
 * Revisions: a revised proposal is a new row with version = parent's version + 1 and parent_id =
 * the row it revises; version 1 rows have no parent (CHECKed).
 */
return new class extends Migration
{
    public const ENVS = ['demo', 'live'];

    /** Brief §7.9: the Orchestrator's cycle state machine. */
    public const CYCLE_STATES = [
        'cycle_start',
        'research_requests_sent',
        'research_collected',
        'proposals_open',
        'reviews_done',
        'voting',
        'vote_closed',
        'risk_check',
        'awaiting_owner',
        'executing',
        'cycle_closed',
    ];

    /** Brief §8: the proposal lifecycle. */
    public const PROPOSAL_STATUSES = [
        'draft',
        'under_review',
        'voting',
        'passed',
        'rejected',
        'risk_checked',
        'awaiting_owner',
        'sent',
        'partially_filled',
        'filled',
        'closed',
        'cancelled',
        'expired',
    ];

    /** Entry proposals come from the Strategist; the rest from the Portfolio Manager (§7.8). */
    public const PROPOSAL_KINDS = ['entry', 'amend_stop', 'amend_targets', 'partial_close', 'close'];

    public const SIDES = ['buy', 'sell'];

    public const ORDER_TYPES = ['market', 'limit', 'post_only'];

    public const VOTES = ['take', 'skip', 'change'];

    public const RISK_RESULTS = ['approved', 'resized', 'rejected'];

    public const RISK_DECISIONS = ['send_now', 'awaiting_owner'];

    public function up(): void
    {
        Schema::create('cycles', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            // What started it, e.g. scheduled, manual (/cycle now), alert. Free text: the
            // Orchestrator task owns the list.
            $table->string('kind', 32);
            $table->enum('state', self::CYCLE_STATES)->default('cycle_start');
            $table->timestampTz('started_at', 6);
            // Step => ISO-8601 UTC deadline, e.g. {"research": "...Z", "vote": "...Z"}.
            $table->jsonb('deadlines')->default('{}');
            $table->timestampTz('closed_at', 6)->nullable();
            $table->timestampsTz(6);

            $table->index(['env', 'started_at']);
        });

        Schema::create('proposals', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->foreignUuid('cycle_id')->nullable()->constrained('cycles')->nullOnDelete();
            $table->integer('version')->default(1);
            // The row this one revises (FK added below, once the primary key exists).
            $table->uuid('parent_id')->nullable();
            $table->enum('kind', self::PROPOSAL_KINDS)->default('entry');
            // Agent slug (strategist, portfolio_manager) or "owner".
            $table->string('author', 64);
            $table->string('inst_id', 64);
            $table->enum('side', self::SIDES)->nullable();
            $table->enum('order_type', self::ORDER_TYPES)->nullable();
            $table->decimal('entry', 38, 18)->nullable();
            // [low, high] as decimal strings, or null.
            $table->jsonb('entry_zone')->nullable();
            $table->decimal('stop', 38, 18)->nullable();
            // [{"price": "…", "pct": "50"}, …] decimal strings.
            $table->jsonb('take_profits')->default('[]');
            $table->integer('time_stop_minutes')->nullable();
            // % of equity the author suggests; the Risk Engine decides the size.
            $table->decimal('size_suggestion_pct', 38, 18)->nullable();
            $table->integer('leverage_suggestion')->nullable();
            $table->decimal('expected_r', 38, 18)->nullable();
            $table->decimal('probability', 6, 4)->nullable();
            $table->text('rationale_md')->default('');
            $table->text('invalidation')->default('');
            // [{"type": "research_report"|"news_item"|"stat", "id": "…"}, …].
            $table->jsonb('evidence')->default('[]');
            $table->enum('status', self::PROPOSAL_STATUSES)->default('draft');
            $table->timestampsTz(6);

            $table->index(['env', 'inst_id', 'status']);
            $table->index(['env', 'created_at']);
            $table->unique('parent_id');
        });

        Schema::table('proposals', function (Blueprint $table): void {
            $table->foreign('parent_id')->references('id')->on('proposals')->restrictOnDelete();
        });

        Schema::create('votes', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->foreignUuid('proposal_id')->constrained('proposals')->cascadeOnDelete();
            $table->string('agent', 64);
            $table->enum('vote', self::VOTES);
            $table->decimal('confidence', 6, 4);
            $table->text('reason')->default('');
            $table->timestampsTz(6);

            $table->unique(['proposal_id', 'agent']);
        });

        Schema::create('risk_checks', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->foreignUuid('proposal_id')->constrained('proposals')->cascadeOnDelete();
            // settings.version the checks ran against (rule 2: limits come only from Settings).
            $table->integer('settings_version');
            $table->enum('result', self::RISK_RESULTS);
            // One entry per §10 check, in order: [{"check": "…", "passed": true, "detail": "…", …}].
            $table->jsonb('checks')->default('[]');
            // Contracts after lotSz rounding; null when rejected.
            $table->decimal('final_size', 38, 18)->nullable();
            $table->decimal('final_notional_usd', 38, 18)->nullable();
            $table->integer('leverage')->nullable();
            $table->text('reason')->nullable();
            // §10 check 9; null when the result is rejected.
            $table->enum('decision', self::RISK_DECISIONS)->nullable();
            $table->timestampsTz(6);

            $table->index(['proposal_id', 'created_at']);
        });

        DB::statement('ALTER TABLE proposals ADD CONSTRAINT proposals_version_positive CHECK (version >= 1)');
        DB::statement('ALTER TABLE proposals ADD CONSTRAINT proposals_parent_matches_version CHECK ((version = 1) = (parent_id IS NULL))');
        DB::statement('ALTER TABLE proposals ADD CONSTRAINT proposals_probability_range CHECK (probability BETWEEN 0 AND 1)');
        DB::statement('ALTER TABLE proposals ADD CONSTRAINT proposals_size_suggestion_range CHECK (size_suggestion_pct > 0 AND size_suggestion_pct <= 100)');
        DB::statement('ALTER TABLE proposals ADD CONSTRAINT proposals_leverage_positive CHECK (leverage_suggestion >= 1)');
        DB::statement('ALTER TABLE proposals ADD CONSTRAINT proposals_time_stop_positive CHECK (time_stop_minutes > 0)');
        // Rule 3: an entry always carries a side, an order type and a stop.
        DB::statement("ALTER TABLE proposals ADD CONSTRAINT proposals_entry_complete CHECK (kind <> 'entry' OR (side IS NOT NULL AND order_type IS NOT NULL AND stop IS NOT NULL))");
        DB::statement("ALTER TABLE proposals ADD CONSTRAINT proposals_amend_stop_has_stop CHECK (kind <> 'amend_stop' OR stop IS NOT NULL)");
        DB::statement('ALTER TABLE votes ADD CONSTRAINT votes_confidence_range CHECK (confidence BETWEEN 0 AND 1)');
        DB::statement('ALTER TABLE risk_checks ADD CONSTRAINT risk_checks_settings_version_positive CHECK (settings_version >= 1)');
        DB::statement('ALTER TABLE risk_checks ADD CONSTRAINT risk_checks_leverage_positive CHECK (leverage >= 1)');
        DB::statement("ALTER TABLE risk_checks ADD CONSTRAINT risk_checks_decision_matches_result CHECK ((result = 'rejected') = (decision IS NULL))");
    }

    public function down(): void
    {
        Schema::dropIfExists('risk_checks');
        Schema::dropIfExists('votes');
        Schema::dropIfExists('proposals');
        Schema::dropIfExists('cycles');
    }
};
