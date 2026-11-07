<?php

declare(strict_types=1);

use App\Models\Setting;
use App\Models\User;
use Carbon\CarbonImmutable;
use Illuminate\Testing\TestResponse;

/*
 * ATL-163: `blocklist_until` ({instId: expiry}) for the composer's `/block <instId> today`, and
 * atlas:expire-blocks, which lifts an entry once its expiry passes.
 */

beforeEach(function (): void {
    $this->travelTo(CarbonImmutable::parse('2026-11-01T10:00:00Z'));
    $this->user = User::factory()->create();
    $this->actingAs($this->user, 'web');
    $this->getJson('/api/settings?env=demo')->assertOk();
});

function atl163Patch(object $test, array $changes): TestResponse
{
    $version = Setting::query()->where('env', 'demo')->value('version');

    return $test->patchJson('/api/settings', ['env' => 'demo', 'version' => $version, 'changes' => $changes]);
}

it('defaults to no expiries', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk()->assertJsonPath('data.settings.blocklist_until', []);
});

it('stores an expiry in UTC with an upper-case instId key', function (): void {
    atl163Patch($this, [
        'blocklist' => ['XAU-USDT-SWAP'],
        'blocklist_until' => ['xau-usdt-swap' => '2026-11-01T23:59:59+03:00'],
    ])->assertOk()
        ->assertJsonPath('data.settings.blocklist', ['XAU-USDT-SWAP'])
        ->assertJsonPath('data.settings.blocklist_until', ['XAU-USDT-SWAP' => '2026-11-01T20:59:59Z']);
});

it('drops an expiry for an instrument that is not on the blocklist', function (): void {
    atl163Patch($this, [
        'blocklist' => ['XAU-USDT-SWAP'],
        'blocklist_until' => ['XAU-USDT-SWAP' => '2026-11-01T20:59:59Z', 'BTC-USDT-SWAP' => '2026-11-01T20:59:59Z'],
    ])->assertOk()->assertJsonPath('data.settings.blocklist_until', ['XAU-USDT-SWAP' => '2026-11-01T20:59:59Z']);

    // Unblocking the instrument drops its expiry too.
    atl163Patch($this, ['blocklist' => []])->assertOk()->assertJsonPath('data.settings.blocklist_until', []);
});

it('refuses a malformed or past expiry with 422', function (array $until): void {
    atl163Patch($this, ['blocklist' => ['XAU-USDT-SWAP'], 'blocklist_until' => $until])
        ->assertUnprocessable()
        ->assertJsonValidationErrors('changes.blocklist_until');
})->with([
    'not a time' => [['XAU-USDT-SWAP' => 'tonight']],
    'no time zone' => [['XAU-USDT-SWAP' => '2026-11-01T20:59:59']],
    'in the past' => [['XAU-USDT-SWAP' => '2026-11-01T09:59:59Z']],
    'key not an instId' => [['XAU USDT' => '2026-11-01T20:59:59Z']],
    'a list, not a map' => [['2026-11-01T20:59:59Z']],
]);

it('lifts an expired block through a new settings version and keeps the others', function (): void {
    atl163Patch($this, [
        'blocklist' => ['XAU-USDT-SWAP', 'DOGE-USDT-SWAP', 'BTC-USDT-SWAP'],
        'blocklist_until' => ['XAU-USDT-SWAP' => '2026-11-01T20:59:59Z', 'BTC-USDT-SWAP' => '2026-11-02T20:59:59Z'],
    ])->assertOk();
    $version = Setting::query()->where('env', 'demo')->value('version');

    $this->artisan('atlas:expire-blocks')->assertSuccessful();
    expect(Setting::query()->where('env', 'demo')->value('version'))->toBe($version);

    $this->travelTo(CarbonImmutable::parse('2026-11-01T21:00:00Z'));
    $this->artisan('atlas:expire-blocks')->assertSuccessful();

    $setting = Setting::query()->where('env', 'demo')->firstOrFail();
    expect($setting->version)->toBe($version + 1)
        ->and($setting->data['blocklist'])->toBe(['DOGE-USDT-SWAP', 'BTC-USDT-SWAP'])
        ->and($setting->data['blocklist_until'])->toBe(['BTC-USDT-SWAP' => '2026-11-02T20:59:59Z']);
});
