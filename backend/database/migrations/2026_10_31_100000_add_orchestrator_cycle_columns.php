<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * ATL-160, brief §7.9/§8: the Orchestrator's cycle state machine.
 *
 * cycles:
 * - slot_at: the scheduled slot (UTC) a `scheduled` cycle runs for; unique per env so a slot runs
 *   once even across a restart. Null for manual (/cycle now) cycles.
 * - transitions: state => ISO-8601 UTC time the cycle entered it.
 * - timeouts: [{"step", "agent", "inst_id"?, "deadline", "at"}] — every late agent the cycle went on without.
 * - outcome / outcome_reason: how the cycle closed (e.g. no_new_proposals + why).
 *
 * proposals:
 * - approval_expires_at: when an awaiting_owner proposal stops being approvable (ATL-162 expires it).
 * - status `advise_only`: risk-approved at autonomy 1; never sent (not an Executor status).
 */
return new class extends Migration
{
    public const PROPOSAL_STATUSES = [
        'draft', 'under_review', 'voting', 'passed', 'rejected', 'risk_checked', 'awaiting_owner',
        'advise_only', 'sent', 'partially_filled', 'filled', 'closed', 'cancelled', 'expired',
    ];

    public function up(): void
    {
        Schema::table('cycles', function (Blueprint $table): void {
            $table->timestampTz('slot_at', 6)->nullable()->after('started_at');
            $table->jsonb('transitions')->default('{}')->after('deadlines');
            $table->jsonb('timeouts')->default('[]')->after('transitions');
            $table->string('outcome', 64)->nullable()->after('timeouts');
            $table->text('outcome_reason')->nullable()->after('outcome');
            $table->unique(['env', 'slot_at']);
        });
        Schema::table('proposals', function (Blueprint $table): void {
            $table->timestampTz('approval_expires_at', 6)->nullable()->after('status');
        });
        $this->statuses(self::PROPOSAL_STATUSES);
    }

    public function down(): void
    {
        DB::table('proposals')->where('status', 'advise_only')->update(['status' => 'expired']);
        $this->statuses(array_values(array_diff(self::PROPOSAL_STATUSES, ['advise_only'])));
        Schema::table('proposals', function (Blueprint $table): void {
            $table->dropColumn('approval_expires_at');
        });
        Schema::table('cycles', function (Blueprint $table): void {
            $table->dropUnique(['env', 'slot_at']);
            $table->dropColumn(['slot_at', 'transitions', 'timeouts', 'outcome', 'outcome_reason']);
        });
    }

    /** @param list<string> $values */
    private function statuses(array $values): void
    {
        $list = implode(', ', array_map(static fn (string $v): string => "'".$v."'", $values));
        DB::statement('ALTER TABLE proposals DROP CONSTRAINT IF EXISTS proposals_status_check');
        DB::statement("ALTER TABLE proposals ADD CONSTRAINT proposals_status_check CHECK (status IN ({$list}))");
    }
};
