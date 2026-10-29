<?php

declare(strict_types=1);

use App\Support\News\NewsSearch;
use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/**
 * ATL-67: GIN expression index for /api/news?q= full-text search over title + summary.
 * The expression is NewsSearch::TS_DOCUMENT, which the API query repeats verbatim.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::statement('CREATE INDEX IF NOT EXISTS news_items_fts_gin ON news_items USING gin (('.NewsSearch::TS_DOCUMENT.'))');
    }

    public function down(): void
    {
        DB::statement('DROP INDEX IF EXISTS news_items_fts_gin');
    }
};
