<?php

declare(strict_types=1);

use App\Models\Setting;
use App\Models\SettingsHistory;
use App\Models\User;

/** ATL-124: money limits (§14.4), markets (§14.5) and Risk Engine parameters (§10) in Settings. */
beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
});

it('returns the money-limit, markets and risk-engine defaults', function (): void {
    $this->getJson('/api/settings?env=demo')
        ->assertOk()
        ->assertJsonPath('data.settings.bot_capital_cap_usdt', '10000')
        ->assertJsonPath('data.settings.max_usdt_per_trade', '1000')
        ->assertJsonPath('data.settings.max_new_exposure_per_day_usdt', '5000')
        ->assertJsonPath('data.settings.max_trades_per_day', 10)
        ->assertJsonPath('data.settings.enabled_markets', ['spot', 'crypto_perp', 'stock_perp', 'commodity_perp'])
        ->assertJsonPath('data.settings.whitelist', [])
        ->assertJsonPath('data.settings.blocklist', [])
        ->assertJsonPath('data.settings.avoid_stock_perps_outside_us_hours', true)
        ->assertJsonPath('data.settings.min_stop_atr_mult', '0.8')
        ->assertJsonPath('data.settings.max_stop_pct', '8')
        ->assertJsonPath('data.settings.slippage_bps', '2')
        ->assertJsonPath('data.settings.liq_buffer_mult', '2.0')
        ->assertJsonPath('data.settings.block_new_trades_minutes_before_high_impact_event', 15)
        ->assertJsonPath('data.settings.volatility_halt_sigma', '6')
        ->assertJsonPath('data.settings.market_data_max_age_seconds', 30)
        ->assertJsonPath('data.settings.margin_buffer', '0.9');
});

it('fills the new keys from the defaults for a row saved before they existed', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $setting = Setting::query()->where('env', 'demo')->firstOrFail();
    $data = $setting->data;
    unset($data['max_usdt_per_trade'], $data['enabled_markets'], $data['margin_buffer']);
    $setting->data = $data;
    $setting->save();

    $this->getJson('/api/settings?env=demo')
        ->assertOk()
        ->assertJsonPath('data.settings.max_usdt_per_trade', '1000')
        ->assertJsonPath('data.settings.enabled_markets', ['spot', 'crypto_perp', 'stock_perp', 'commodity_perp'])
        ->assertJsonPath('data.settings.margin_buffer', '0.9');
});

it('refuses invalid values and unknown keys with 422 and writes no version', function (array $changes, string $error): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $historyBefore = SettingsHistory::query()->count();

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => $changes])
        ->assertUnprocessable()
        ->assertJsonValidationErrors($error);

    expect(Setting::query()->where('env', 'demo')->value('version'))->toBe(1)
        ->and(SettingsHistory::query()->count())->toBe($historyBefore);
})->with([
    'negative per-trade cap' => [['max_usdt_per_trade' => '-1'], 'changes.max_usdt_per_trade'],
    'zero per-trade cap' => [['max_usdt_per_trade' => '0'], 'changes.max_usdt_per_trade'],
    'per-trade cap as a float' => [['max_usdt_per_trade' => 1000.5], 'changes.max_usdt_per_trade'],
    'non-numeric atr mult' => [['min_stop_atr_mult' => 'abc'], 'changes.min_stop_atr_mult'],
    'unknown key' => [['max_usdt_per_tarde' => '100'], 'changes'],
    'unknown key beside a valid one' => [['max_usdt_per_trade' => '500', 'risk_limit' => '1'], 'changes'],
    'trades per day 0' => [['max_trades_per_day' => 0], 'changes.max_trades_per_day'],
    'trades per day as a string' => [['max_trades_per_day' => '5'], 'changes.max_trades_per_day'],
    'margin buffer above 1' => [['margin_buffer' => '1.1'], 'changes.margin_buffer'],
    'liq buffer below 1' => [['liq_buffer_mult' => '0.5'], 'changes.liq_buffer_mult'],
    'max stop 0' => [['max_stop_pct' => '0'], 'changes.max_stop_pct'],
    'data age 0' => [['market_data_max_age_seconds' => 0], 'changes.market_data_max_age_seconds'],
    'blackout minutes negative' => [['block_new_trades_minutes_before_high_impact_event' => -1], 'changes.block_new_trades_minutes_before_high_impact_event'],
    'us hours as a string' => [['avoid_stock_perps_outside_us_hours' => 'yes'], 'changes.avoid_stock_perps_outside_us_hours'],
    'unknown market' => [['enabled_markets' => ['spot', 'forex']], 'changes.enabled_markets.1'],
    'duplicate market' => [['enabled_markets' => ['spot', 'spot']], 'changes.enabled_markets.0'],
    'markets not a list' => [['enabled_markets' => 'spot'], 'changes.enabled_markets'],
    'blocklist entry not an instId' => [['blocklist' => ['BTC USDT']], 'changes.blocklist.0'],
    'whitelist entry not a string' => [['whitelist' => [42]], 'changes.whitelist.0'],
]);

it('versions a valid max_usdt_per_trade change and records its history', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $historyBefore = SettingsHistory::query()->count();

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['max_usdt_per_trade' => '2500.50']])
        ->assertOk()
        ->assertJsonPath('data.version', 2)
        ->assertJsonPath('data.settings.max_usdt_per_trade', '2500.50');

    $history = SettingsHistory::query()->latest('version')->firstOrFail();
    expect(SettingsHistory::query()->count())->toBe($historyBefore + 1)
        ->and($history->version)->toBe(2)
        ->and($history->diff)->toEqual(['max_usdt_per_trade' => ['from' => '1000', 'to' => '2500.50']])
        ->and($history->data['max_usdt_per_trade'])->toBe('2500.50');
});

it('stores markets toggles and instrument lists in canonical form', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => [
        'enabled_markets' => ['options', 'crypto_perp'],
        'blocklist' => ['xau-usdt-swap', 'XAU-USDT-SWAP', ' btc-usdt '],
        'whitelist' => ['SOL-USDT-SWAP'],
        'avoid_stock_perps_outside_us_hours' => false,
        'slippage_bps' => '0',
    ]])
        ->assertOk()
        ->assertJsonPath('data.settings.enabled_markets', ['crypto_perp', 'options'])
        ->assertJsonPath('data.settings.blocklist', ['XAU-USDT-SWAP', 'BTC-USDT'])
        ->assertJsonPath('data.settings.whitelist', ['SOL-USDT-SWAP'])
        ->assertJsonPath('data.settings.avoid_stock_perps_outside_us_hours', false)
        ->assertJsonPath('data.settings.slippage_bps', '0');

    // Every toggle off is a valid choice.
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 2, 'changes' => ['enabled_markets' => []]])
        ->assertOk()
        ->assertJsonPath('data.settings.enabled_markets', []);
});
