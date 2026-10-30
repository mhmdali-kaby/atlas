<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Market Scout's daily watchlists (brief §5, §7.1; decision #247 (3), ATL-102).
 *
 * `date` is a DATE: the Asia/Baghdad business day the watchlist is for, never a timestamp, so
 * "today's watchlist" cannot drift by the UTC offset. A re-run (intraday or /cycle now) writes a new
 * row with the next `version`; the current watchlist for (env, date) is the highest version, and
 * unique (env, date, version) keeps that one row unambiguous. Python's save_watchlist writes both
 * tables; the bridge does not.
 */
return new class extends Migration
{
    public const ENVS = ['demo', 'live'];

    public const STATUSES = ['draft', 'published', 'partial_evidence', 'empty'];

    public const BIASES = ['long', 'short', 'neutral'];

    public function up(): void
    {
        Schema::create('watchlists', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            // Asia/Baghdad calendar date (business day) the watchlist covers.
            $table->date('date');
            $table->integer('version');
            // Who wrote it: an agent slug (e.g. market_scout) or "owner".
            $table->string('created_by', 64);
            $table->enum('status', self::STATUSES);
            $table->timestampTz('edit_window_ends_at', 6)->nullable();
            // Why: "no watchlist today" reason, or what evidence was missing for partial_evidence.
            $table->text('reason')->nullable();
            // The warroom_messages id of the watchlist card. No FK: the bridge persists that
            // message asynchronously, possibly after this row is written.
            $table->uuid('message_id')->nullable();
            // Backdrop note, rejected candidates and anything else the card carries beside the picks.
            $table->jsonb('data')->default('{}');
            $table->timestampsTz(6);

            $table->unique(['env', 'date', 'version']);
            $table->index(['env', 'date', 'created_at']);
        });

        Schema::create('watchlist_items', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->foreignUuid('watchlist_id')->constrained('watchlists')->cascadeOnDelete();
            $table->string('inst_id', 64);
            $table->integer('rank');
            $table->string('asset_class', 32);
            $table->enum('bias', self::BIASES);
            $table->decimal('conviction', 6, 4);
            $table->text('thesis');
            $table->jsonb('catalysts')->default('[]');
            $table->jsonb('key_levels')->default('{}');
            $table->text('invalidation');
            $table->boolean('pinned_by_owner')->default(false);
            $table->timestampTz('removed_by_owner_at', 6)->nullable();
            $table->timestampTz('data_as_of', 6);
            $table->timestampsTz(6);

            $table->unique(['watchlist_id', 'rank']);
            $table->unique(['watchlist_id', 'inst_id']);
        });

        DB::statement('ALTER TABLE watchlists ADD CONSTRAINT watchlists_version_positive CHECK (version >= 1)');
        DB::statement('ALTER TABLE watchlist_items ADD CONSTRAINT watchlist_items_rank_positive CHECK (rank >= 1)');
        DB::statement('ALTER TABLE watchlist_items ADD CONSTRAINT watchlist_items_conviction_range CHECK (conviction BETWEEN 0 AND 1)');
    }

    public function down(): void
    {
        Schema::dropIfExists('watchlist_items');
        Schema::dropIfExists('watchlists');
    }
};
