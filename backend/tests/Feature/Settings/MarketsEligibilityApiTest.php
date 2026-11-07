<?php

declare(strict_types=1);

use App\Models\Setting;
use App\Models\SettingsHistory;
use App\Models\User;
use App\Support\Settings\SettingsService;
use App\Support\Settings\TickerSpreads;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;
use Illuminate\Testing\TestResponse;

/** ATL-155, decision #250 (1): GET /api/settings/markets/eligibility. */

/**
 * @param  array<string, mixed>  $overrides
 */
function atl155Instrument(string $instId, array $overrides = []): void
{
    DB::table('instruments')->insert(array_merge([
        'id' => (string) Str::uuid(),
        'env' => 'demo',
        'inst_id' => $instId,
        'inst_type' => 'SWAP',
        'base_ccy' => explode('-', $instId)[0],
        'quote_ccy' => null,
        'settle_ccy' => 'USDT',
        'lot_sz' => '1',
        'min_sz' => '1',
        'tick_sz' => '0.01',
        'state' => 'live',
        'asset_class' => 'crypto',
        'list_time' => now()->subDays(400),
        'created_at' => now()->subDays(400),
        'updated_at' => now(),
    ], $overrides));
}

/** 24 closed 1H bars ending at the last closed hour, each with $quotePerBar of volCcyQuote. */
function atl155Volume(string $instId, string $quotePerBar): void
{
    $lastOpen = CarbonImmutable::now('UTC')->startOfHour()->subHour();
    $rows = [];
    for ($i = 0; $i < 24; $i++) {
        $rows[] = [
            'env' => 'demo', 'inst_id' => $instId, 'bar' => '1H', 'ts' => $lastOpen->subHours($i),
            'o' => '100', 'h' => '101', 'l' => '99', 'c' => '100', 'vol' => '1',
            'vol_ccy' => '1', 'vol_ccy_quote' => $quotePerBar, 'confirmed' => true,
        ];
    }
    DB::table('candles')->insert($rows);
}

/**
 * @param  array<string, string>  $spreads  instId => bps
 */
function atl155Spreads(array $spreads): void
{
    app()->instance(TickerSpreads::class, new class($spreads) extends TickerSpreads
    {
        /** @param array<string, string> $fixed */
        public function __construct(private readonly array $fixed) {}

        public function spreadsBps(string $env, array $instIds, CarbonImmutable $now, int $maxAgeSeconds): array
        {
            return array_intersect_key($this->fixed, array_flip($instIds));
        }
    });
}

/**
 * @param  array<string, mixed>  $query
 */
function atl155Get(array $query): TestResponse
{
    return test()->getJson('/api/settings/markets/eligibility?'.http_build_query(['env' => 'demo', ...$query]));
}

beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    atl155Spreads([]);
});

it('requires a login', function (): void {
    auth()->guard('web')->logout();
    $this->getJson('/api/settings/markets/eligibility?env=demo&inst_ids[]=BTC-USDT-SWAP')->assertUnauthorized();
});

it('puts blocked first for an instrument on both the whitelist and the blocklist', function (): void {
    atl155Instrument('XAU-USDT-SWAP', ['asset_class' => 'commodity']);
    atl155Volume('XAU-USDT-SWAP', '1000000');
    app(SettingsService::class)->current('demo');
    app(SettingsService::class)->update('demo', 1, ['whitelist' => ['XAU-USDT-SWAP'], 'blocklist' => ['XAU-USDT-SWAP']]);

    $row = atl155Get(['inst_ids' => ['XAU-USDT-SWAP']])->assertOk()->json('data.instruments.0');

    expect($row['eligible'])->toBeFalse()
        ->and($row['fails'][0])->toBe('blocked')
        ->and($row['on_whitelist'])->toBeTrue()
        ->and($row['market'])->toBe('commodity_perp');
});

it('fails a live crypto perp on min_24h_volume and passes one above it', function (): void {
    atl155Instrument('THIN-USDT-SWAP');
    atl155Volume('THIN-USDT-SWAP', '100000'); // 2.4M over 24h, below the 20M crypto_perp default
    atl155Instrument('BTC-USDT-SWAP');
    atl155Volume('BTC-USDT-SWAP', '5000000'); // 120M
    atl155Spreads(['BTC-USDT-SWAP' => '0.0123']);

    $rows = atl155Get(['inst_ids' => ['THIN-USDT-SWAP', 'btc-usdt-swap']])->assertOk()->json('data.instruments');

    expect($rows[0])->toMatchArray([
        'inst_id' => 'THIN-USDT-SWAP', 'eligible' => false, 'fails' => ['min_24h_volume'],
        'volume_24h_usd' => '2400000', 'min_24h_volume_usd' => '20000000', 'notes' => ['spread_unknown'],
    ])->and($rows[1])->toMatchArray([
        'inst_id' => 'BTC-USDT-SWAP', 'eligible' => true, 'fails' => [], 'spread_bps' => '0.0123', 'notes' => [],
    ]);
});

it('says no_data for an instrument with no stored ticker data', function (): void {
    atl155Instrument('NEW-USDT-SWAP');

    atl155Get(['inst_ids' => ['NEW-USDT-SWAP']])
        ->assertOk()
        ->assertJsonPath('data.instruments.0.fails', ['no_data'])
        ->assertJsonPath('data.instruments.0.volume_24h_usd', null);
});

it('fails on max_spread when the live spread is wider than the setting', function (): void {
    atl155Instrument('BTC-USDT-SWAP');
    atl155Volume('BTC-USDT-SWAP', '5000000');
    atl155Spreads(['BTC-USDT-SWAP' => '12.5']);

    atl155Get(['inst_ids' => ['BTC-USDT-SWAP']])->assertOk()->assertJsonPath('data.instruments.0.fails', ['max_spread']);
});

it('reports every failing filter in the documented order', function (): void {
    atl155Instrument('AAPL-USDT-SWAP', [
        'asset_class' => 'stock', 'state' => 'suspend', 'list_time' => now()->subDays(2), 'created_at' => now()->subDays(2),
    ]);
    atl155Volume('AAPL-USDT-SWAP', '1');
    atl155Spreads(['AAPL-USDT-SWAP' => '50']);
    app(SettingsService::class)->current('demo');
    app(SettingsService::class)->update('demo', 1, ['enabled_markets' => ['crypto_perp'], 'blocklist' => ['AAPL-USDT-SWAP']]);

    atl155Get(['inst_ids' => ['AAPL-USDT-SWAP']])->assertOk()->assertJsonPath('data.instruments.0.fails', [
        'blocked', 'asset_class_disabled', 'not_live', 'listed_under_7_days', 'min_24h_volume', 'max_spread',
    ]);
});

it('treats an empty enabled_markets list as every market off', function (): void {
    atl155Instrument('BTC-USDT-SWAP');
    atl155Volume('BTC-USDT-SWAP', '5000000');

    atl155Get(['inst_ids' => ['BTC-USDT-SWAP'], 'draft' => ['enabled_markets' => '']])
        ->assertOk()
        ->assertJsonPath('data.instruments.0.fails', ['asset_class_disabled']);
});

it('previews a draft minimum volume without writing a settings version', function (): void {
    atl155Instrument('THIN-USDT-SWAP');
    atl155Volume('THIN-USDT-SWAP', '100000');
    app(SettingsService::class)->current('demo');
    $history = SettingsHistory::query()->count();

    atl155Get(['inst_ids' => ['THIN-USDT-SWAP']])->assertJsonPath('data.instruments.0.fails', ['min_24h_volume']);
    atl155Get(['inst_ids' => ['THIN-USDT-SWAP'], 'draft' => ['min_24h_volume_usd_crypto_perp' => '1000000']])
        ->assertOk()
        ->assertJsonPath('data.instruments.0.eligible', true)
        ->assertJsonPath('data.instruments.0.min_24h_volume_usd', '1000000')
        ->assertJsonPath('data.draft', ['min_24h_volume_usd_crypto_perp'])
        ->assertJsonPath('data.settings_version', 1);

    expect(Setting::query()->where('env', 'demo')->value('version'))->toBe(1)
        ->and(Setting::query()->where('env', 'demo')->value('data')['min_24h_volume_usd_crypto_perp'])->toBe('20000000')
        ->and(SettingsHistory::query()->count())->toBe($history);
});

it('creates no settings row when the env has none', function (): void {
    atl155Instrument('BTC-USDT-SWAP');

    atl155Get(['inst_ids' => ['BTC-USDT-SWAP']])->assertOk()->assertJsonPath('data.settings_version', null);

    expect(Setting::query()->count())->toBe(0);
});

it('returns unknown_instrument for an instId the env does not list', function (): void {
    atl155Instrument('BTC-USDT-SWAP', ['env' => 'live']);

    atl155Get(['inst_ids' => ['BTC-USDT-SWAP']])
        ->assertOk()
        ->assertJsonPath('data.instruments.0.fails', ['unknown_instrument'])
        ->assertJsonPath('data.instruments.0.eligible', false);
});

it('refuses bad requests with 422', function (array $query, string $error): void {
    atl155Get($query)->assertUnprocessable()->assertJsonValidationErrors($error);
})->with([
    'more than 50 ids' => [['inst_ids' => array_map(fn (int $i): string => "C{$i}-USDT-SWAP", range(1, 51))], 'inst_ids'],
    'no ids' => [[], 'inst_ids'],
    'not an instId' => [['inst_ids' => ['BTC USDT']], 'inst_ids.0'],
    'unknown draft key' => [['inst_ids' => ['BTC-USDT-SWAP'], 'draft' => ['max_usdt_per_trade' => '5']], 'draft'],
    'negative draft volume' => [['inst_ids' => ['BTC-USDT-SWAP'], 'draft' => ['min_24h_volume_usd_spot' => '-1']], 'draft.min_24h_volume_usd_spot'],
    'unknown draft market' => [['inst_ids' => ['BTC-USDT-SWAP'], 'draft' => ['enabled_markets' => ['forex']]], 'draft.enabled_markets.0'],
    'bad env' => [['env' => 'paper', 'inst_ids' => ['BTC-USDT-SWAP']], 'env'],
]);

it('reads the spread from a fresh ticker only', function (): void {
    $now = CarbonImmutable::parse('2026-10-30T08:00:00Z');
    $ticker = json_encode(['bidPx' => '99.99', 'askPx' => '100.01']);

    expect(TickerSpreads::spread(['ticker' => $ticker, 'tickers_ms' => (string) $now->subSeconds(5)->getTimestampMs()], $now, 30))->toBe('2.0000')
        ->and(TickerSpreads::spread(['ticker' => $ticker, 'tickers_ms' => (string) $now->subSeconds(31)->getTimestampMs()], $now, 30))->toBeNull()
        ->and(TickerSpreads::spread(['tickers_ms' => (string) $now->getTimestampMs()], $now, 30))->toBeNull()
        ->and(TickerSpreads::spread(['ticker' => json_encode(['bidPx' => '', 'askPx' => '1']), 'tickers_ms' => (string) $now->getTimestampMs()], $now, 30))->toBeNull();
});
