<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Setting;
use App\Models\User;
use App\Models\Watchlist;
use App\Models\WatchlistItem;
use App\Support\Settings\SettingsService;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Str;

function atl103Instrument(string $instId, string $state = 'live', string $env = 'demo', string $assetClass = 'crypto'): void
{
    DB::table('instruments')->insert([
        'id' => (string) Str::uuid(),
        'env' => $env, 'inst_id' => $instId, 'inst_type' => 'SWAP',
        'lot_sz' => '1', 'min_sz' => '1', 'tick_sz' => '0.1', 'state' => $state,
        'asset_class' => $assetClass, 'created_at' => now(), 'updated_at' => now(),
    ]);
}

/**
 * @param  list<array{0: string, 1: int}>  $picks  [inst_id, rank]
 */
function atl103Watchlist(string $date, int $version, array $picks, ?CarbonImmutable $windowEnds = null, string $env = 'demo', string $status = 'published'): Watchlist
{
    $watchlist = Watchlist::query()->create([
        'env' => $env, 'date' => $date, 'version' => $version, 'created_by' => 'market_scout',
        'status' => $status, 'edit_window_ends_at' => $windowEnds ?? CarbonImmutable::now()->addMinutes(10),
        'data' => ['backdrop' => 'BTC trending up'],
    ]);
    foreach ($picks as [$instId, $rank]) {
        $watchlist->items()->create([
            'inst_id' => $instId, 'rank' => $rank, 'asset_class' => 'crypto', 'bias' => 'long',
            'conviction' => '0.7100', 'thesis' => "{$instId} thesis", 'catalysts' => [],
            'key_levels' => ['support' => 100.5], 'invalidation' => 'close below 98',
            'data_as_of' => CarbonImmutable::now()->subMinutes(5),
        ]);
    }

    return $watchlist;
}

/**
 * @return list<array<string, mixed>>
 */
function atl103Events(): array
{
    /** @var list<array{0: string, 1: list<string>}> $entries */
    $entries = Redis::connection('events')->command('executeRaw', [['XRANGE', 'atlas.warroom', '-', '+']]);

    return array_values(array_filter(
        array_map(fn (array $e): array => json_decode($e[1][1], true, flags: JSON_THROW_ON_ERROR), $entries),
        fn (array $env): bool => $env['type'] === 'watchlist.updated',
    ));
}

function atl103Today(): string
{
    return CarbonImmutable::now('Asia/Baghdad')->toDateString();
}

beforeEach(function (): void {
    Redis::connection('events')->command('executeRaw', [['DEL', 'atlas.warroom']]);
    $this->user = User::factory()->create();
    $this->actingAs($this->user, 'web');
});

it('returns the day\'s latest version with items in rank order', function (): void {
    $day = atl103Today();
    atl103Watchlist($day, 1, [['ETH-USDT-SWAP', 1]]);
    $latest = atl103Watchlist($day, 2, [['XAU-USDT-SWAP', 3], ['BTC-USDT-SWAP', 1], ['TSLA-USDT-SWAP', 2]]);
    atl103Watchlist($day, 3, [['SOL-USDT-SWAP', 1]], env: 'live');

    $response = $this->getJson('/api/watchlists')->assertOk();

    expect($response->json('date'))->toBe($day)
        ->and($response->json('env'))->toBe('demo')
        ->and($response->json('watchlist.id'))->toBe($latest->id)
        ->and($response->json('watchlist.version'))->toBe(2)
        ->and($response->json('watchlist.status'))->toBe('published')
        ->and($response->json('watchlist.edit_window_open'))->toBeTrue()
        ->and($response->json('watchlist.edit_window_ends_at'))->toBeString()
        ->and(array_column($response->json('watchlist.items'), 'inst_id'))
        ->toBe(['BTC-USDT-SWAP', 'TSLA-USDT-SWAP', 'XAU-USDT-SWAP'])
        ->and($response->json('watchlist.removed_items'))->toBe([]);
});

it('reads a given date and returns watchlist null on a day with none', function (): void {
    atl103Watchlist('2026-10-01', 1, [['BTC-USDT-SWAP', 1]]);

    $this->getJson('/api/watchlists?date=2026-10-01')->assertOk()
        ->assertJsonPath('watchlist.date', '2026-10-01');

    $this->getJson('/api/watchlists?date=2026-10-02')->assertOk()
        ->assertExactJson(['env' => 'demo', 'date' => '2026-10-02', 'timezone' => 'Asia/Baghdad', 'watchlist' => null]);

    $this->getJson('/api/watchlists?date=01-10-2026')->assertStatus(422)->assertJsonValidationErrors('date');
});

it('removes and restores an item inside the window, auditing and publishing each', function (): void {
    $watchlist = atl103Watchlist(atl103Today(), 1, [['BTC-USDT-SWAP', 1], ['ETH-USDT-SWAP', 2], ['XAU-USDT-SWAP', 3]]);
    $eth = $watchlist->items()->where('inst_id', 'ETH-USDT-SWAP')->firstOrFail();

    $removed = $this->postJson("/api/watchlists/{$watchlist->id}/items/{$eth->id}/remove")->assertOk();
    expect(array_column($removed->json('watchlist.items'), 'inst_id'))->toBe(['BTC-USDT-SWAP', 'XAU-USDT-SWAP'])
        ->and($removed->json('watchlist.removed_items.0.id'))->toBe($eth->id)
        ->and($eth->fresh()?->removed_by_owner_at)->not->toBeNull();

    // Removing it twice is a conflict and writes nothing more.
    $this->postJson("/api/watchlists/{$watchlist->id}/items/{$eth->id}/remove")
        ->assertStatus(409)->assertJsonPath('error', 'item_already_removed');

    $restored = $this->postJson("/api/watchlists/{$watchlist->id}/items/{$eth->id}/restore")->assertOk();
    expect(array_column($restored->json('watchlist.items'), 'inst_id'))->toBe(['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'XAU-USDT-SWAP'])
        ->and($restored->json('item.rank'))->toBe(2)
        ->and($eth->fresh()?->removed_by_owner_at)->toBeNull();

    $audits = AuditLog::query()->where('subject_id', $eth->id)->orderBy('created_at')->get();
    expect($audits->pluck('action')->all())->toBe(['watchlist.item_removed', 'watchlist.item_restored'])
        ->and($audits[0]->user_id)->toBe($this->user->id);

    $events = atl103Events();
    expect($events)->toHaveCount(2)
        ->and($events[0]['source'])->toBe('human:owner')
        ->and($events[0]['env'])->toBe('demo')
        ->and($events[0]['payload']['action'])->toBe('removed')
        ->and($events[0]['payload']['watchlist_id'])->toBe($watchlist->id)
        ->and($events[0]['payload']['item']['inst_id'])->toBe('ETH-USDT-SWAP')
        ->and(array_column($events[0]['payload']['items'], 'inst_id'))->toBe(['BTC-USDT-SWAP', 'XAU-USDT-SWAP'])
        ->and($events[1]['payload']['action'])->toBe('restored')
        ->and(array_keys($events[1]))->toBe(['id', 'type', 'ts', 'env', 'source', 'correlation_id', 'payload']);
});

it('pins a live instrument inside the window as an owner-pinned item at the next rank', function (): void {
    atl103Instrument('SOL-USDT-SWAP');
    $watchlist = atl103Watchlist(atl103Today(), 1, [['BTC-USDT-SWAP', 1], ['ETH-USDT-SWAP', 2]]);

    $response = $this->postJson("/api/watchlists/{$watchlist->id}/pin", ['inst_id' => 'sol-usdt-swap'])->assertOk();

    expect($response->json('item.inst_id'))->toBe('SOL-USDT-SWAP')
        ->and($response->json('item.rank'))->toBe(3)
        ->and($response->json('item.pinned_by_owner'))->toBeTrue()
        ->and($response->json('item.asset_class'))->toBe('crypto')
        ->and(array_column($response->json('watchlist.items'), 'inst_id'))->toBe(['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP']);

    $item = WatchlistItem::query()->where('watchlist_id', $watchlist->id)->where('inst_id', 'SOL-USDT-SWAP')->firstOrFail();
    expect($item->pinned_by_owner)->toBeTrue();
    expect(AuditLog::query()->where('action', 'watchlist.item_pinned')->where('subject_id', $item->id)->count())->toBe(1);

    $events = atl103Events();
    expect($events)->toHaveCount(1)
        ->and($events[0]['payload']['action'])->toBe('pinned')
        ->and($events[0]['payload']['item']['inst_id'])->toBe('SOL-USDT-SWAP');

    // Pinning one already on the list marks it pinned instead of adding a row.
    atl103Instrument('BTC-USDT-SWAP');
    $this->postJson("/api/watchlists/{$watchlist->id}/pin", ['inst_id' => 'BTC-USDT-SWAP'])->assertOk()
        ->assertJsonPath('item.rank', 1)->assertJsonPath('item.pinned_by_owner', true);
    expect($watchlist->items()->count())->toBe(3);
});

it('gives a pinned item a rank after removed ones, so a restore keeps its old rank', function (): void {
    atl103Instrument('SOL-USDT-SWAP');
    $watchlist = atl103Watchlist(atl103Today(), 1, [['BTC-USDT-SWAP', 1], ['ETH-USDT-SWAP', 2]]);
    $eth = $watchlist->items()->where('inst_id', 'ETH-USDT-SWAP')->firstOrFail();

    $this->postJson("/api/watchlists/{$watchlist->id}/items/{$eth->id}/remove")->assertOk();
    $this->postJson("/api/watchlists/{$watchlist->id}/pin", ['inst_id' => 'SOL-USDT-SWAP'])->assertOk()
        ->assertJsonPath('item.rank', 3);
    $this->postJson("/api/watchlists/{$watchlist->id}/items/{$eth->id}/restore")->assertOk()
        ->assertJsonPath('item.rank', 2);
});

it('refuses remove, restore and pin with 409 edit_window_closed after the window and changes nothing', function (): void {
    // Pinned to a fixed instant, not an offset from the real clock (ATL-181): the old
    // `now()->addMinutes(11)` crossed into the next Asia/Baghdad business day whenever the
    // suite happened to run between 23:49 and midnight there, so "today"'s watchlist lookup
    // at the end of the test silently stopped finding this one. travelTo keeps the whole test
    // on one fixed business day regardless of the real wall clock, whatever it is faked to.
    $this->travelTo(CarbonImmutable::parse('2026-06-15 10:00:00', 'Asia/Baghdad'));
    atl103Instrument('SOL-USDT-SWAP');
    $watchlist = atl103Watchlist(atl103Today(), 1, [['BTC-USDT-SWAP', 1], ['ETH-USDT-SWAP', 2]]);
    $btc = $watchlist->items()->where('inst_id', 'BTC-USDT-SWAP')->firstOrFail();
    $eth = $watchlist->items()->where('inst_id', 'ETH-USDT-SWAP')->firstOrFail();
    $this->postJson("/api/watchlists/{$watchlist->id}/items/{$eth->id}/remove")->assertOk();
    Redis::connection('events')->command('executeRaw', [['DEL', 'atlas.warroom']]);
    $auditsBefore = AuditLog::query()->count();

    $this->travelTo(CarbonImmutable::now()->addMinutes(11));
    $this->postJson("/api/watchlists/{$watchlist->id}/items/{$btc->id}/remove")
        ->assertStatus(409)->assertJsonPath('error', 'edit_window_closed')->assertJsonPath('ok', false);
    $this->postJson("/api/watchlists/{$watchlist->id}/pin", ['inst_id' => 'SOL-USDT-SWAP'])
        ->assertStatus(409)->assertJsonPath('error', 'edit_window_closed');
    $this->postJson("/api/watchlists/{$watchlist->id}/items/{$eth->id}/restore")
        ->assertStatus(409)->assertJsonPath('error', 'edit_window_closed');

    $this->getJson('/api/watchlists')->assertOk()->assertJsonPath('watchlist.edit_window_open', false);

    expect($btc->fresh()?->removed_by_owner_at)->toBeNull()
        ->and($eth->fresh()?->removed_by_owner_at)->not->toBeNull()
        ->and($watchlist->items()->count())->toBe(2)
        ->and(AuditLog::query()->count())->toBe($auditsBefore)
        ->and(atl103Events())->toBe([]);
});

it('uses created_at plus the configured minutes when edit_window_ends_at is null', function (): void {
    $watchlist = atl103Watchlist(atl103Today(), 1, [['BTC-USDT-SWAP', 1]]);
    $watchlist->forceFill(['edit_window_ends_at' => null])->save();
    $watchlist->created_at = CarbonImmutable::now()->subMinutes(11);
    $watchlist->save();
    $btc = $watchlist->items()->firstOrFail();

    expect(config('atlas_defaults.agents.scout.watchlist_edit_window_minutes'))->toBe(10);
    $this->postJson("/api/watchlists/{$watchlist->id}/items/{$btc->id}/remove")
        ->assertStatus(409)->assertJsonPath('error', 'edit_window_closed');
});

it('refuses edits to an older version with 409 not_current_version', function (): void {
    $day = atl103Today();
    $old = atl103Watchlist($day, 1, [['BTC-USDT-SWAP', 1]]);
    atl103Watchlist($day, 2, [['ETH-USDT-SWAP', 1]]);
    $btc = $old->items()->firstOrFail();

    $this->postJson("/api/watchlists/{$old->id}/items/{$btc->id}/remove")
        ->assertStatus(409)->assertJsonPath('error', 'not_current_version');
    expect($btc->fresh()?->removed_by_owner_at)->toBeNull();
});

it('returns 404 for an item of another watchlist', function (): void {
    $day = atl103Today();
    $a = atl103Watchlist($day, 1, [['BTC-USDT-SWAP', 1]]);
    $b = atl103Watchlist($day, 1, [['ETH-USDT-SWAP', 1]], env: 'live');
    $ethOfB = $b->items()->firstOrFail();

    $this->postJson("/api/watchlists/{$a->id}/items/{$ethOfB->id}/remove")->assertNotFound();
});

it('refuses to pin an unknown, non-live or blocklisted instrument with 422 on inst_id', function (): void {
    atl103Instrument('OLD-USDT-SWAP', state: 'suspend');
    atl103Instrument('LIVEONLY-USDT-SWAP', env: 'live');
    atl103Instrument('XAU-USDT-SWAP', assetClass: 'commodity');
    atl103Instrument('DOGE-USDT-SWAP');
    $watchlist = atl103Watchlist(atl103Today(), 1, [['BTC-USDT-SWAP', 1]]);
    $url = "/api/watchlists/{$watchlist->id}/pin";

    $this->postJson($url, ['inst_id' => 'NOPE-USDT-SWAP'])->assertStatus(422)->assertJsonValidationErrors('inst_id');
    $this->postJson($url, ['inst_id' => 'LIVEONLY-USDT-SWAP'])->assertStatus(422)->assertJsonValidationErrors('inst_id');
    $this->postJson($url, ['inst_id' => 'OLD-USDT-SWAP'])->assertStatus(422)->assertJsonValidationErrors('inst_id');
    $this->postJson($url, [])->assertStatus(422)->assertJsonValidationErrors('inst_id');

    // Blocklist from config (the default when settings has none) …
    config(['atlas_defaults.agents.scout.universe.blocklist' => ['xau-usdt-swap']]);
    $this->postJson($url, ['inst_id' => 'XAU-USDT-SWAP'])->assertStatus(422)
        ->assertJsonValidationErrors(['inst_id' => 'blocklist']);

    // … and from the settings row, which wins over config.
    $setting = app(SettingsService::class)->current('demo');
    Setting::query()->whereKey($setting->id)->update(['data' => json_encode([...$setting->data, 'blocklist' => ['DOGE-USDT-SWAP']])]);
    $this->postJson($url, ['inst_id' => 'DOGE-USDT-SWAP'])->assertStatus(422)->assertJsonValidationErrors('inst_id');
    $this->postJson($url, ['inst_id' => 'XAU-USDT-SWAP'])->assertOk();

    expect($watchlist->items()->count())->toBe(2)
        ->and(atl103Events())->toHaveCount(1);
});

it('returns 401 to an unauthenticated request on every route', function (): void {
    $watchlist = atl103Watchlist(atl103Today(), 1, [['BTC-USDT-SWAP', 1]]);
    $item = $watchlist->items()->firstOrFail();
    auth()->guard('web')->logout();

    $this->getJson('/api/watchlists')->assertUnauthorized();
    $this->postJson("/api/watchlists/{$watchlist->id}/items/{$item->id}/remove")->assertUnauthorized();
    $this->postJson("/api/watchlists/{$watchlist->id}/items/{$item->id}/restore")->assertUnauthorized();
    $this->postJson("/api/watchlists/{$watchlist->id}/pin", ['inst_id' => 'BTC-USDT-SWAP'])->assertUnauthorized();
    expect($item->fresh()?->removed_by_owner_at)->toBeNull();
});
