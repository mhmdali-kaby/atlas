<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * News, calendar and source-registry tables for patch C (decision #243, brief §5, §7.2).
 *
 * news_items is deduplicated on a stored sha256 of canonical_url (a btree on the URL itself
 * would fail on very long URLs); Python writes it with `ON CONFLICT (canonical_url_hash)`.
 * Blueprint has no text[] or vector type, so those columns and their GIN/HNSW indexes are raw SQL.
 */
return new class extends Migration
{
    public const SOURCE_KINDS = ['rss', 'api', 'calendar', 'earnings', 'sentiment'];

    /** Brief §7.2 step 4. */
    public const EVENT_TYPES = [
        'earnings', 'guidance', 'macro_release', 'central_bank', 'regulation', 'hack_exploit',
        'listing', 'delisting', 'ETF_flow', 'M&A', 'lawsuit', 'product', 'partnership',
        'token_unlock', 'geopolitics', 'supply_shock', 'other',
    ];

    public const ALIAS_TARGET_KINDS = ['instrument_family', 'asset_class'];

    public function up(): void
    {
        Schema::create('news_sources', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->string('key', 64)->unique();
            $table->enum('kind', self::SOURCE_KINDS);
            $table->string('name', 100);
            $table->text('url')->nullable();
            $table->boolean('enabled')->default(true);
            $table->integer('poll_seconds');
            $table->boolean('requires_key')->default(false);
            // Laravel `encrypted` cast ciphertext; decrypt in Python with atlas_shared.laravel_crypt.
            $table->text('api_key')->nullable();
            $table->integer('consecutive_failures')->default(0);
            $table->timestampTz('disabled_until', 6)->nullable();
            $table->timestampTz('last_success_at', 6)->nullable();
            $table->timestampTz('last_item_at', 6)->nullable();
            $table->text('last_error')->nullable();
            $table->timestampsTz(6);
        });

        Schema::create('news_clusters', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->text('headline');
            $table->timestampTz('first_seen', 6);
            $table->timestampTz('last_seen', 6);
            $table->integer('item_count')->default(0);
            $table->smallInteger('max_importance')->default(1);
            $table->timestampsTz(6);

            $table->index('first_seen');
        });

        Schema::create('news_items', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->string('source', 64);
            $table->text('url');
            $table->text('canonical_url');
            $table->text('title');
            $table->text('title_original')->nullable();
            $table->text('summary')->nullable();
            $table->text('body')->nullable();
            $table->timestampTz('published_at', 6);
            $table->timestampTz('fetched_at', 6);
            $table->string('language', 16)->nullable();
            $table->jsonb('entities')->nullable();
            $table->enum('event_type', self::EVENT_TYPES)->nullable();
            $table->decimal('sentiment', 6, 4)->nullable();
            $table->smallInteger('importance')->default(1);
            $table->decimal('novelty', 6, 4)->nullable();
            $table->foreignUuid('cluster_id')->nullable()->constrained('news_clusters')->nullOnDelete();
            $table->jsonb('raw')->nullable();
            $table->timestampsTz(6);

            $table->index('published_at');
            $table->index('source');
        });

        Schema::create('economic_events', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->string('source', 64);
            $table->string('external_id', 191);
            $table->string('country', 16);
            $table->string('name', 255);
            $table->smallInteger('importance')->default(1);
            $table->timestampTz('scheduled_at', 6);
            // As published ("3.2%", "245K"); the numeric comparison lives in surprise.
            $table->string('actual', 64)->nullable();
            $table->string('forecast', 64)->nullable();
            $table->string('previous', 64)->nullable();
            $table->decimal('surprise', 38, 18)->nullable();
            $table->timestampsTz(6);

            $table->unique(['source', 'external_id']);
            $table->index('scheduled_at');
        });

        Schema::create('earnings_events', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->string('symbol', 32);
            $table->date('date');
            // Finnhub hour: bmo (before open), amc (after close), dmh (during hours) or empty.
            $table->string('time', 16)->nullable();
            $table->decimal('eps_est', 38, 18)->nullable();
            $table->decimal('eps_act', 38, 18)->nullable();
            $table->decimal('rev_est', 38, 18)->nullable();
            $table->decimal('rev_act', 38, 18)->nullable();
            $table->timestampsTz(6);

            $table->unique(['symbol', 'date']);
            $table->index('date');
        });

        Schema::create('market_sentiment', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->string('index_key', 64);
            $table->decimal('value', 38, 18);
            $table->string('classification', 64)->nullable();
            $table->timestampTz('ts', 6);
            $table->timestampsTz(6);

            $table->unique(['index_key', 'ts']);
        });

        Schema::create('instrument_aliases', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->string('alias', 191);
            $table->string('alias_normalized', 191);
            $table->enum('target_kind', self::ALIAS_TARGET_KINDS);
            $table->string('target', 64);
            $table->smallInteger('priority')->default(0);
            $table->timestampsTz(6);

            $table->unique(['alias_normalized', 'target']);
        });

        DB::statement('ALTER TABLE news_items ADD CONSTRAINT news_items_importance_check CHECK (importance BETWEEN 1 AND 5)');
        DB::statement('ALTER TABLE news_items ADD CONSTRAINT news_items_sentiment_check CHECK (sentiment BETWEEN -1 AND 1)');
        DB::statement('ALTER TABLE news_clusters ADD CONSTRAINT news_clusters_max_importance_check CHECK (max_importance BETWEEN 1 AND 5)');
        DB::statement('ALTER TABLE economic_events ADD CONSTRAINT economic_events_importance_check CHECK (importance BETWEEN 1 AND 5)');

        DB::statement("ALTER TABLE news_items ADD COLUMN instruments text[] NOT NULL DEFAULT '{}'");
        DB::statement("ALTER TABLE news_items ADD COLUMN asset_classes text[] NOT NULL DEFAULT '{}'");
        DB::statement("ALTER TABLE news_clusters ADD COLUMN instruments text[] NOT NULL DEFAULT '{}'");
        DB::statement('ALTER TABLE news_items ADD COLUMN embedding vector(384)');
        // convert_to() is only STABLE, so a generated column cannot call it directly. The UTF-8
        // bytes of a text value never change for a given database, so the wrapper is IMMUTABLE.
        DB::statement(<<<'SQL'
            CREATE OR REPLACE FUNCTION atlas_sha256_text(value text) RETURNS bytea
            LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
            AS $$ SELECT sha256(convert_to(value, 'UTF8')) $$
        SQL);
        DB::statement(
            'ALTER TABLE news_items ADD COLUMN canonical_url_hash bytea GENERATED ALWAYS AS (atlas_sha256_text(canonical_url)) STORED'
        );

        DB::statement('ALTER TABLE news_items ADD CONSTRAINT news_items_canonical_url_hash_unique UNIQUE (canonical_url_hash)');
        DB::statement('CREATE INDEX news_items_instruments_gin ON news_items USING gin (instruments)');
        DB::statement('CREATE INDEX news_items_embedding_hnsw ON news_items USING hnsw (embedding vector_cosine_ops)');
        DB::statement('CREATE INDEX news_clusters_instruments_gin ON news_clusters USING gin (instruments)');
    }

    public function down(): void
    {
        Schema::dropIfExists('instrument_aliases');
        Schema::dropIfExists('market_sentiment');
        Schema::dropIfExists('earnings_events');
        Schema::dropIfExists('economic_events');
        Schema::dropIfExists('news_items');
        Schema::dropIfExists('news_clusters');
        Schema::dropIfExists('news_sources');
        DB::statement('DROP FUNCTION IF EXISTS atlas_sha256_text(text)');
    }
};
