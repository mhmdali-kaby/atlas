<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * ATL-153, brief §7.7/§7.8/§7.9: a Portfolio Manager partial_close carries the % of the position to
 * close (proposals.close_pct, 0 < x < 100, required for partial_close), and a "change" vote carries
 * the fix the voter asks for (votes.fix), which the vote weighting checks was applied.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('proposals', function (Blueprint $table): void {
            $table->decimal('close_pct', 38, 18)->nullable()->after('size_suggestion_pct');
        });
        Schema::table('votes', function (Blueprint $table): void {
            $table->text('fix')->nullable()->after('reason');
        });
        DB::statement('ALTER TABLE proposals ADD CONSTRAINT proposals_close_pct_range CHECK (close_pct > 0 AND close_pct < 100)');
        DB::statement("ALTER TABLE proposals ADD CONSTRAINT proposals_partial_close_has_pct CHECK (kind <> 'partial_close' OR close_pct IS NOT NULL)");
    }

    public function down(): void
    {
        DB::statement('ALTER TABLE proposals DROP CONSTRAINT IF EXISTS proposals_partial_close_has_pct');
        DB::statement('ALTER TABLE proposals DROP CONSTRAINT IF EXISTS proposals_close_pct_range');
        Schema::table('votes', function (Blueprint $table): void {
            $table->dropColumn('fix');
        });
        Schema::table('proposals', function (Blueprint $table): void {
            $table->dropColumn('close_pct');
        });
    }
};
