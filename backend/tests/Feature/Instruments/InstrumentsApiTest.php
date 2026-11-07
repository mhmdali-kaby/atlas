<?php

declare(strict_types=1);

use App\Models\Setting;
use App\Models\User;
use App\Support\Settings\SettingsService;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

/**
 * @param  array<string, mixed>  $overrides
 */
function atl119Instrument(array $overrides = []): void
{
    DB::table('instruments')->insert(array_merge([
        'id' => (string) Str::uuid(),
        'env' => 'demo',
        'inst_id' => 'SOL-USDT-SWAP',
        'inst_type' => 'SWAP',
        'base_ccy' => 'SOL',
        'quote_ccy' => 'USDT',
        'settle_ccy' => 'USDT',
        'lot_sz' => '1',
        'min_sz' => '1',
        'tick_sz' => '0.01',
        'state' => 'live',
        'asset_class' => 'crypto',
        'created_at' => now(),
        'updated_at' => now(),
    ], $overrides));
}

beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    config(['atlas_defaults.agents.scout.universe.blocklist' => []]);
});

/**
 * @param  list<string>  $blocklist
 */
function atl119SettingsBlocklist(string $env, array $blocklist): Setting
{
    $setting = app(SettingsService::class)->current($env);
    $setting->forceFill(['data' => [...$setting->data, 'blocklist' => $blocklist]])->save();

    return $setting;
}

it('matches inst_id or base_ccy prefix, case-insensitively, exact base match first', function (): void {
    atl119Instrument(['inst_id' => 'SOL-USDT-SWAP', 'base_ccy' => 'SOL']);
    atl119Instrument(['inst_id' => 'SOL-USDT', 'inst_type' => 'SPOT', 'base_ccy' => 'SOL']);
    // inst_id-only match: starts with "SOL" but base_ccy does not equal it, and sorts
    // alphabetically before the exact-base-match rows, so this proves the ordering rule.
    atl119Instrument(['inst_id' => 'SOL', 'base_ccy' => 'XYZ', 'asset_class' => 'other']);
    atl119Instrument(['inst_id' => 'BTC-USDT-SWAP', 'base_ccy' => 'BTC']);

    $res = $this->getJson('/api/instruments?q=SOL')->assertOk();

    expect(array_column($res->json('data'), 'inst_id'))->toBe(['SOL-USDT', 'SOL-USDT-SWAP', 'SOL']);
    foreach ($res->json('data') as $row) {
        expect($row)->toHaveKeys(['inst_id', 'inst_type', 'asset_class', 'base_ccy', 'quote_ccy', 'settle_ccy', 'blocked']);
    }
});

it('excludes suspended instruments and the other env, and marks a blocklisted instrument blocked', function (): void {
    atl119Instrument(['inst_id' => 'SOL-USDT-SWAP', 'base_ccy' => 'SOL', 'state' => 'live']);
    atl119Instrument(['inst_id' => 'SOLX-USDT-SWAP', 'base_ccy' => 'SOLX', 'state' => 'suspend']);
    atl119Instrument(['inst_id' => 'SOL-USDT-SWAP', 'env' => 'live', 'base_ccy' => 'SOL']);
    atl119Instrument(['inst_id' => 'SOLV-USDT-SWAP', 'base_ccy' => 'SOLV', 'state' => 'live']);
    config(['atlas_defaults.agents.scout.universe.blocklist' => ['SOLV-USDT-SWAP']]);

    $res = $this->getJson('/api/instruments?q=sol&env=demo')->assertOk();

    expect(array_column($res->json('data'), 'inst_id'))->toBe(['SOL-USDT-SWAP', 'SOLV-USDT-SWAP']);
    expect($res->json('data.0.blocked'))->toBeFalse();
    expect($res->json('data.1.blocked'))->toBeTrue();
});

it('marks blocked from the settings-data blocklist even when the config list is empty', function (): void {
    atl119Instrument(['inst_id' => 'SOL-USDT-SWAP', 'base_ccy' => 'SOL', 'state' => 'live']);
    atl119Instrument(['inst_id' => 'SOLV-USDT-SWAP', 'base_ccy' => 'SOLV', 'state' => 'live']);
    config(['atlas_defaults.agents.scout.universe.blocklist' => []]);
    atl119SettingsBlocklist('demo', ['SOLV-USDT-SWAP']);

    $res = $this->getJson('/api/instruments?q=sol&env=demo')->assertOk();

    expect(array_column($res->json('data'), 'inst_id'))->toBe(['SOL-USDT-SWAP', 'SOLV-USDT-SWAP']);
    expect($res->json('data.0.blocked'))->toBeFalse();
    expect($res->json('data.1.blocked'))->toBeTrue();
});

it('matches a lower-case blocklist entry, from settings data as from config', function (): void {
    atl119Instrument(['inst_id' => 'SOLV-USDT-SWAP', 'base_ccy' => 'SOLV', 'state' => 'live']);
    atl119SettingsBlocklist('demo', ['solv-usdt-swap']);

    $res = $this->getJson('/api/instruments?q=sol&env=demo')->assertOk();

    expect($res->json('data.0.inst_id'))->toBe('SOLV-USDT-SWAP')
        ->and($res->json('data.0.blocked'))->toBeTrue();
});

it('filters by asset_class', function (): void {
    atl119Instrument(['inst_id' => 'XAU-USDT-SWAP', 'base_ccy' => 'XAU', 'asset_class' => 'commodity']);
    atl119Instrument(['inst_id' => 'XAG-USDT-SWAP', 'base_ccy' => 'XAG', 'asset_class' => 'commodity']);
    atl119Instrument(['inst_id' => 'XAI-USDT-SWAP', 'base_ccy' => 'XAI', 'asset_class' => 'crypto']);

    $res = $this->getJson('/api/instruments?q=xa&asset_class=commodity')->assertOk();

    expect(array_column($res->json('data'), 'inst_id'))->toBe(['XAG-USDT-SWAP', 'XAU-USDT-SWAP']);
});

it('rejects a limit above 50', function (): void {
    $this->getJson('/api/instruments?q=sol&limit=51')
        ->assertUnprocessable()
        ->assertJsonValidationErrors('limit');
});

it('rejects an empty q', function (): void {
    $this->getJson('/api/instruments?q=')
        ->assertUnprocessable()
        ->assertJsonValidationErrors('q');
});

it('rejects a 33-character q', function (): void {
    $this->getJson('/api/instruments?q='.str_repeat('a', 33))
        ->assertUnprocessable()
        ->assertJsonValidationErrors('q');
});

it('defaults the limit to 20 and never returns more', function (): void {
    for ($i = 0; $i < 25; $i++) {
        atl119Instrument(['inst_id' => sprintf('SOL%02d-USDT-SWAP', $i), 'base_ccy' => sprintf('SOL%02d', $i)]);
    }

    $res = $this->getJson('/api/instruments?q=sol')->assertOk();

    expect($res->json('data'))->toHaveCount(20);
});

it('rejects an unauthenticated request', function (): void {
    auth()->guard('web')->logout();

    $this->getJson('/api/instruments?q=sol')->assertUnauthorized();
});

it('ATL-250: q=xau returns only the active env\'s instruments whose inst_id or base contains it, case-insensitive', function (): void {
    DB::table('system_state')->updateOrInsert(['id' => 1], ['active_env' => 'live']);
    atl119Instrument(['env' => 'live', 'inst_id' => 'XAU-USDT-SWAP', 'base_ccy' => 'XAU', 'asset_class' => 'commodity']);
    atl119Instrument(['env' => 'live', 'inst_id' => 'XAUT-USDT-SWAP', 'base_ccy' => 'XAUT', 'asset_class' => 'commodity']);
    // base contains "xau" but does not start with it.
    atl119Instrument(['env' => 'live', 'inst_id' => 'PXAUX-USDT-SWAP', 'base_ccy' => 'pXAUx', 'asset_class' => 'other']);
    atl119Instrument(['env' => 'live', 'inst_id' => 'XAG-USDT-SWAP', 'base_ccy' => 'XAG', 'asset_class' => 'commodity']);
    // the other env's gold is not listed.
    atl119Instrument(['env' => 'demo', 'inst_id' => 'XAU-USDT-SWAP', 'base_ccy' => 'XAU', 'asset_class' => 'commodity']);

    $res = $this->getJson('/api/instruments?q=xau')->assertOk();

    expect(array_column($res->json('data'), 'inst_id'))->toBe(['XAU-USDT-SWAP', 'XAUT-USDT-SWAP', 'PXAUX-USDT-SWAP'])
        ->and($res->json('data.0.state'))->toBe('live')
        ->and($res->json('data.0.asset_class'))->toBe('commodity');

    $upper = $this->getJson('/api/instruments?q=XaU')->assertOk();
    expect(array_column($upper->json('data'), 'inst_id'))->toBe(['XAU-USDT-SWAP', 'XAUT-USDT-SWAP', 'PXAUX-USDT-SWAP']);
});

it('ATL-250: returns at most 20 contains-matches', function (): void {
    for ($i = 0; $i < 25; $i++) {
        atl119Instrument(['inst_id' => sprintf('A%02dXAU-USDT-SWAP', $i), 'base_ccy' => sprintf('A%02dXAU', $i)]);
    }

    expect($this->getJson('/api/instruments?q=xau')->assertOk()->json('data'))->toHaveCount(20);
});

it('ATL-250: state=any includes suspended and pre-open instruments with their state', function (): void {
    atl119Instrument(['inst_id' => 'XAU-USDT-SWAP', 'base_ccy' => 'XAU', 'state' => 'live']);
    atl119Instrument(['inst_id' => 'XAUT-USDT-SWAP', 'base_ccy' => 'XAUT', 'state' => 'preopen']);
    atl119Instrument(['inst_id' => 'XAUX-USDT-SWAP', 'base_ccy' => 'XAUX', 'state' => 'suspend']);

    expect(array_column($this->getJson('/api/instruments?q=xau')->json('data'), 'inst_id'))->toBe(['XAU-USDT-SWAP']);

    $res = $this->getJson('/api/instruments?q=xau&state=any')->assertOk();
    expect(array_column($res->json('data'), 'state', 'inst_id'))->toBe([
        'XAU-USDT-SWAP' => 'live', 'XAUT-USDT-SWAP' => 'preopen', 'XAUX-USDT-SWAP' => 'suspend',
    ]);

    $this->getJson('/api/instruments?q=xau&state=open')->assertUnprocessable()->assertJsonValidationErrors('state');
});
