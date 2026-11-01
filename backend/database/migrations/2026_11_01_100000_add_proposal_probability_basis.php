<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * ATL-189, brief §7.6 step 3: how the Strategist reached its probability.
 *
 * proposals.probability_basis (jsonb, nullable, entries only): {analog_hit_rate (0-1, string),
 * n (int), adjustment_pts (string, |x| <= 15), reason (untrusted model text)}. Validated by
 * atlas_shared.proposals.ProbabilityBasis before it is written.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('proposals', function (Blueprint $table): void {
            $table->jsonb('probability_basis')->nullable()->after('probability');
        });
    }

    public function down(): void
    {
        Schema::table('proposals', function (Blueprint $table): void {
            $table->dropColumn('probability_basis');
        });
    }
};
