<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/**
 * ATL-127, brief §10 check 9 and §14.6: autonomy level 1 ("Advise only") gives the decision
 * advise_only, beside send_now and awaiting_owner. Laravel's enum() is a CHECK constraint named
 * risk_checks_decision_check on Postgres, so it is replaced with the three-value list.
 */
return new class extends Migration
{
    public const RISK_DECISIONS = ['send_now', 'awaiting_owner', 'advise_only'];

    public function up(): void
    {
        $this->replace(self::RISK_DECISIONS);
    }

    public function down(): void
    {
        DB::table('risk_checks')->where('decision', 'advise_only')->delete();
        $this->replace(['send_now', 'awaiting_owner']);
    }

    /** @param list<string> $values */
    private function replace(array $values): void
    {
        $list = implode(', ', array_map(static fn (string $v): string => "'".$v."'", $values));
        DB::statement('ALTER TABLE risk_checks DROP CONSTRAINT IF EXISTS risk_checks_decision_check');
        DB::statement("ALTER TABLE risk_checks ADD CONSTRAINT risk_checks_decision_check CHECK (decision IN ({$list}))");
    }
};
