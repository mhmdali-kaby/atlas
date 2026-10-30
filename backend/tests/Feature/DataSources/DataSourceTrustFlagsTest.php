<?php

declare(strict_types=1);

use App\Models\NewsSource;
use App\Models\User;
use Database\Seeders\NewsSourcesSeeder;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Str;

/*
 * ATL-106 (decision #247 (4)): news_sources.reputable / official, the inputs to a story cluster's
 * code-computed CONFIRMED/REPORTED/RUMOUR status, shown read-only by the Data sources API.
 */

const ATL106_KEY = 'fh-PLAINTEXT-key-trust-flags-9876QRST';

beforeEach(function (): void {
    $this->seed(NewsSourcesSeeder::class);
    $this->owner = User::factory()->create();
    $this->actingAs($this->owner, 'web');
    RateLimiter::clear('data-sources-write|'.$this->owner->id);
});

it('adds both flags as non-null booleans defaulting to false', function (): void {
    expect(Schema::hasColumns('news_sources', ['reputable', 'official']))->toBeTrue();

    $columns = collect(DB::select(<<<'SQL'
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'news_sources'
          AND column_name IN ('reputable', 'official')
    SQL))->keyBy('column_name');

    foreach (['reputable', 'official'] as $name) {
        expect($columns[$name]->data_type)->toBe('boolean')
            ->and($columns[$name]->is_nullable)->toBe('NO')
            ->and($columns[$name]->column_default)->toBe('false');
    }

    // A source the defaults do not know is never trusted.
    DB::table('news_sources')->insert([
        'id' => (string) Str::uuid(), 'key' => 'rss.unknown', 'provider' => 'unknown', 'kind' => 'rss',
        'category' => 'crypto_news', 'name' => 'Unknown', 'poll_seconds' => 300,
        'created_at' => now(), 'updated_at' => now(),
    ]);
    $row = NewsSource::query()->where('key', 'rss.unknown')->sole();
    expect($row->reputable)->toBeFalse()->and($row->official)->toBeFalse();
});

it('seeds the flags from config/defaults.yaml', function (): void {
    /** @var array<string, array{reputable: bool, official: bool}> $sources */
    $sources = (array) config('atlas_defaults.news.sources');
    foreach ($sources as $key => $source) {
        $row = NewsSource::query()->where('key', $key)->sole();
        expect($row->reputable)->toBe($source['reputable'], "{$key} reputable")
            ->and($row->official)->toBe($source['official'], "{$key} official");
    }

    expect(NewsSource::query()->where('key', 'okx.announcements')->sole()->official)->toBeTrue()
        ->and(NewsSource::query()->where('key', 'rss.coindesk')->sole()->reputable)->toBeTrue()
        ->and(NewsSource::query()->where('key', 'finnhub.news')->sole()->reputable)->toBeTrue()
        ->and(NewsSource::query()->where('key', 'cryptopanic')->sole()->reputable)->toBeFalse()
        ->and(NewsSource::query()->where('key', 'cryptopanic')->sole()->official)->toBeFalse();
});

it('backfills existing rows from the defaults when the migration runs', function (): void {
    $migration = require database_path('migrations/2026_10_30_100000_add_reputation_flags_to_news_sources.php');
    $migration->down();
    expect(Schema::hasColumn('news_sources', 'reputable'))->toBeFalse();

    $migration->up();

    expect(DB::table('news_sources')->where('key', 'okx.announcements')->value('official'))->toBeTrue()
        ->and(DB::table('news_sources')->where('key', 'rss.coindesk')->value('reputable'))->toBeTrue()
        ->and(DB::table('news_sources')->where('key', 'cryptopanic')->value('reputable'))->toBeFalse();
});

it('returns both flags read-only in the Data sources API without exposing the api_key', function (): void {
    NewsSource::query()->where('key', 'finnhub.news')->sole()->forceFill(['api_key' => ATL106_KEY])->save();

    $response = $this->getJson('/api/data-sources')->assertOk();
    $rows = collect($response->json('data'))->keyBy('key');

    expect($rows['okx.announcements']['official'])->toBeTrue()
        ->and($rows['okx.announcements']['reputable'])->toBeTrue()
        ->and($rows['finnhub.news']['reputable'])->toBeTrue()
        ->and($rows['finnhub.news']['official'])->toBeFalse()
        ->and($rows['cryptopanic']['reputable'])->toBeFalse()
        ->and($rows['cryptopanic']['official'])->toBeFalse();
    foreach ($rows as $row) {
        expect($row)->not->toHaveKey('api_key')
            ->and($row['reputable'])->toBeBool()
            ->and($row['official'])->toBeBool();
    }
    expect((string) $response->getContent())->not->toContain(ATL106_KEY)
        ->and($rows['finnhub.news']['key_last4'])->toBe('QRST');

    $this->patchJson('/api/data-sources/cryptopanic', ['enabled' => true, 'reputable' => true])
        ->assertUnprocessable()->assertJsonValidationErrors(['reputable']);
    $this->patchJson('/api/data-sources/cryptopanic', ['enabled' => true, 'official' => true])
        ->assertUnprocessable()->assertJsonValidationErrors(['official']);

    $source = NewsSource::query()->where('key', 'cryptopanic')->sole();
    expect($source->reputable)->toBeFalse()->and($source->official)->toBeFalse();
});
