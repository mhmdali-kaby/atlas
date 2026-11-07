<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * ATL-106 (decision #247 (4), brief §7.2): news_sources.reputable and news_sources.official. The
 * agents' news tools label a story cluster from them in code: CONFIRMED when any source is official
 * or 2+ distinct sources are reputable, REPORTED when exactly one is reputable, else RUMOUR.
 * Existing rows take their flags from config/defaults.yaml `news.sources`; rows not in the defaults
 * stay false (unknown sources are never trusted by default).
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('news_sources', function (Blueprint $table): void {
            $table->boolean('reputable')->default(false);
            $table->boolean('official')->default(false);
        });

        /** @var array<string, array{reputable?: bool, official?: bool}> $sources */
        $sources = (array) config('atlas_defaults.news.sources');
        foreach ($sources as $key => $source) {
            DB::table('news_sources')->where('key', $key)->update([
                'reputable' => (bool) ($source['reputable'] ?? false),
                'official' => (bool) ($source['official'] ?? false),
            ]);
        }
    }

    public function down(): void
    {
        Schema::table('news_sources', function (Blueprint $table): void {
            $table->dropColumn(['reputable', 'official']);
        });
    }
};
