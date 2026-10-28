<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\QueryException;
use Illuminate\Support\Facades\DB;

/**
 * Enables the pgvector extension so `php artisan atlas:doctor` (ATL-13) can find it in
 * pg_extension alongside timescaledb. Nothing uses the `vector` column type yet — the
 * news_items.embedding column (brief §5) is Phase 1 — so this is deliberately just the
 * extension, not a table. On non-pgsql drivers (e.g. the local SQLite smoke runner) both
 * steps are skipped, matching the equity_curve hypertable migration.
 */
return new class extends Migration
{
    public function up(): void
    {
        if (DB::connection()->getDriverName() !== 'pgsql') {
            return;
        }

        DB::statement('CREATE EXTENSION IF NOT EXISTS vector');
    }

    public function down(): void
    {
        if (DB::connection()->getDriverName() !== 'pgsql') {
            return;
        }

        // Guarded: once a Phase 1 column uses the vector type, dropping the extension
        // without CASCADE fails with "cannot drop extension ... because other objects
        // depend on it". Swallow that so rollback never crashes on a dependency it
        // doesn't own; the extension is simply left in place in that case.
        try {
            DB::statement('DROP EXTENSION IF EXISTS vector');
        } catch (QueryException) {
            // Left in place — something depends on it.
        }
    }
};
