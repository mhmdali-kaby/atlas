<?php

declare(strict_types=1);

use Illuminate\Database\QueryException;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Str;

/** @return array<string, object{data_type: string, is_nullable: string, column_default: ?string}> */
function watchlistColumns(string $table): array
{
    return collect(DB::select(
        'SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = ?',
        [$table],
    ))->keyBy('column_name')->all();
}

/** @param array<string, mixed> $overrides */
function insertWatchlist(array $overrides = []): string
{
    $id = (string) Str::uuid();
    DB::table('watchlists')->insert(array_merge([
        'id' => $id, 'env' => 'demo', 'date' => '2026-10-31', 'version' => 1,
        'created_by' => 'market_scout', 'status' => 'published',
    ], $overrides));

    return $id;
}

/** @param array<string, mixed> $overrides */
function insertWatchlistItem(string $watchlistId, array $overrides = []): void
{
    DB::table('watchlist_items')->insert(array_merge([
        'id' => (string) Str::uuid(), 'watchlist_id' => $watchlistId, 'inst_id' => 'BTC-USDT-SWAP',
        'rank' => 1, 'asset_class' => 'crypto', 'bias' => 'long', 'conviction' => '0.6',
        'thesis' => 't', 'invalidation' => 'i', 'data_as_of' => '2026-10-31T05:00:00Z',
    ], $overrides));
}

it('creates both watchlist tables', function (): void {
    expect(Schema::hasTable('watchlists'))->toBeTrue()
        ->and(Schema::hasTable('watchlist_items'))->toBeTrue();
});

it('stores the watchlist day as a date, not a timestamp', function (): void {
    $cols = watchlistColumns('watchlists');

    expect($cols['date']->data_type)->toBe('date')
        ->and($cols['date']->is_nullable)->toBe('NO')
        ->and($cols['edit_window_ends_at']->data_type)->toBe('timestamp with time zone')
        ->and($cols['message_id']->data_type)->toBe('uuid')
        ->and($cols['version']->data_type)->toBe('integer');
});

it('gives the item columns their types and defaults', function (): void {
    $cols = watchlistColumns('watchlist_items');

    expect($cols['conviction']->data_type)->toBe('numeric')
        ->and($cols['catalysts']->data_type)->toBe('jsonb')
        ->and($cols['catalysts']->column_default)->toStartWith("'[]'")
        ->and($cols['key_levels']->column_default)->toStartWith("'{}'")
        ->and($cols['pinned_by_owner']->data_type)->toBe('boolean')
        ->and($cols['removed_by_owner_at']->is_nullable)->toBe('YES')
        ->and($cols['data_as_of']->data_type)->toBe('timestamp with time zone');

    $wl = insertWatchlist();
    insertWatchlistItem($wl);
    $row = DB::table('watchlist_items')->where('watchlist_id', $wl)->sole();
    expect($row->catalysts)->toBe('[]')
        ->and($row->key_levels)->toBe('{}')
        ->and($row->pinned_by_owner)->toBeFalse();
});

it('allows re-runs as new versions but not two rows with one version', function (): void {
    insertWatchlist();
    insertWatchlist(['version' => 2]);
    insertWatchlist(['env' => 'live']);

    expect(DB::table('watchlists')->where('date', '2026-10-31')->count())->toBe(3);
    expect(fn () => insertWatchlist())->toThrow(UniqueConstraintViolationException::class);
});

it('rejects values outside the agreed ranges', function (array $watchlist, array $item): void {
    $wl = insertWatchlist($watchlist);
    if ($item !== []) {
        insertWatchlistItem($wl, $item);
    }
})->throws(QueryException::class)->with([
    'status' => [['status' => 'superseded'], []],
    'version 0' => [['version' => 0], []],
    'bias' => [[], ['bias' => 'up']],
    'conviction 1.2' => [[], ['conviction' => '1.2']],
    'rank 0' => [[], ['rank' => 0]],
]);

it('keeps rank and instrument unique within one watchlist and cascades deletes', function (): void {
    $wl = insertWatchlist();
    insertWatchlistItem($wl);

    // Each attempt runs in its own savepoint so the failed insert does not abort the test transaction.
    expect(fn () => DB::transaction(fn () => insertWatchlistItem($wl, ['inst_id' => 'ETH-USDT-SWAP'])))
        ->toThrow(UniqueConstraintViolationException::class);
    expect(fn () => DB::transaction(fn () => insertWatchlistItem($wl, ['rank' => 2])))
        ->toThrow(UniqueConstraintViolationException::class);

    DB::table('watchlists')->where('id', $wl)->delete();
    expect(DB::table('watchlist_items')->where('watchlist_id', $wl)->count())->toBe(0);
});
