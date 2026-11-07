<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/**
 * ATL-107 (brief §7.2, decision #247 (5)): News Intelligence analyses ingest's news.alert
 * envelopes. Those runs are recorded with agent_runs.trigger = 'alert', next to mention, schedule,
 * eval and manual. Laravel's enum() on Postgres is a varchar with the CHECK constraint
 * agent_runs_trigger_check, so the constraint is replaced.
 */
return new class extends Migration
{
    private const BEFORE = ['mention', 'schedule', 'eval', 'manual'];

    private const AFTER = ['mention', 'schedule', 'eval', 'manual', 'alert'];

    public function up(): void
    {
        $this->replaceCheck(self::AFTER);
    }

    public function down(): void
    {
        DB::table('agent_runs')->where('trigger', 'alert')->update(['trigger' => 'manual']);
        $this->replaceCheck(self::BEFORE);
    }

    /**
     * @param  list<string>  $values
     */
    private function replaceCheck(array $values): void
    {
        $list = implode(', ', array_map(static fn (string $v): string => "'{$v}'::character varying", $values));
        DB::statement('ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_trigger_check');
        DB::statement("ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_trigger_check CHECK ((\"trigger\")::text = ANY (ARRAY[{$list}]::text[]))");
    }
};
