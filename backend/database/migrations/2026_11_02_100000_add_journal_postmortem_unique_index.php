<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/**
 * ATL-194, brief §7.8 POST-MORTEM: one post-mortem per closed position.
 *
 * A post-mortem journal entry carries data.position_ref ("<pos_id>:<closed_at ISO>", one OKX
 * positions-history record). The partial unique index makes (env, proposal_id, position_ref) unique
 * for those rows, so a retried or concurrent hook can never write a second entry for one close.
 * Other journal entries (no position_ref) are unaffected.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::statement(
            "CREATE UNIQUE INDEX IF NOT EXISTS journal_entries_postmortem_unique
             ON journal_entries (env, proposal_id, (data->>'position_ref'))
             WHERE (data->>'position_ref') IS NOT NULL"
        );
    }

    public function down(): void
    {
        DB::statement('DROP INDEX IF EXISTS journal_entries_postmortem_unique');
    }
};
