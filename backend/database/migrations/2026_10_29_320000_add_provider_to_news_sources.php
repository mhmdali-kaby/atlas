<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * ATL-71: news_sources.provider — the vendor that owns a source's API key (finnhub, cryptopanic,
 * okx, forexfactory, alternative_me, or the RSS outlet). One key per provider: Settings → Data
 * sources writes it to every source of that provider. news_items.fetched_at gets an index for the
 * per-source 24h hourly counts.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('news_sources', function (Blueprint $table): void {
            $table->string('provider', 64)->nullable();
        });

        /** @var array<string, array{provider: string}> $sources */
        $sources = (array) config('atlas_defaults.news.sources');
        foreach ($sources as $key => $source) {
            DB::table('news_sources')->where('key', $key)->update(['provider' => $source['provider']]);
        }
        // Rows not in defaults (none in a seeded install): rss.<outlet> → outlet, else the key's first segment.
        DB::statement(<<<'SQL'
            UPDATE news_sources
            SET provider = CASE WHEN key LIKE 'rss.%' THEN substr(key, 5) ELSE split_part(key, '.', 1) END
            WHERE provider IS NULL
        SQL);

        Schema::table('news_sources', function (Blueprint $table): void {
            $table->string('provider', 64)->nullable(false)->change();
            $table->index('provider');
        });

        Schema::table('news_items', function (Blueprint $table): void {
            $table->index('fetched_at');
        });
    }

    public function down(): void
    {
        Schema::table('news_items', function (Blueprint $table): void {
            $table->dropIndex(['fetched_at']);
        });
        Schema::table('news_sources', function (Blueprint $table): void {
            $table->dropIndex(['provider']);
            $table->dropColumn('provider');
        });
    }
};
