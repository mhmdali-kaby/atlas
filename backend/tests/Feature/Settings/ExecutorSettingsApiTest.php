<?php

declare(strict_types=1);

use App\Models\SettingsHistory;
use App\Models\User;

/** ATL-141: Executor parameters (brief §4.4, §11) in Settings. */
beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
});

it('returns the executor defaults', function (): void {
    $this->getJson('/api/settings?env=demo')
        ->assertOk()
        ->assertJsonPath('data.settings.entry_timeout_minutes', 30)
        ->assertJsonPath('data.settings.td_mode_perps', 'isolated')
        ->assertJsonPath('data.settings.stop_verify_seconds', 3)
        ->assertJsonPath('data.settings.order_max_retries', 3)
        ->assertJsonPath('data.settings.emergency_stop_atr_mult', '2.0');
});

it('saves valid executor values as a new version', function (): void {
    $version = $this->getJson('/api/settings?env=demo')->json('data.version');

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => $version, 'changes' => [
        'td_mode_perps' => 'cross', 'entry_timeout_minutes' => 45, 'emergency_stop_atr_mult' => '1.5',
    ]])
        ->assertOk()
        ->assertJsonPath('data.version', $version + 1)
        ->assertJsonPath('data.settings.td_mode_perps', 'cross')
        ->assertJsonPath('data.settings.entry_timeout_minutes', 45)
        ->assertJsonPath('data.settings.emergency_stop_atr_mult', '1.5');
});

it('refuses invalid executor values with 422 and writes no version', function (array $changes, string $error): void {
    $version = $this->getJson('/api/settings?env=demo')->json('data.version');
    $before = SettingsHistory::query()->count();

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => $version, 'changes' => $changes])
        ->assertStatus(422)
        ->assertJsonValidationErrors([$error]);

    expect(SettingsHistory::query()->count())->toBe($before);
})->with([
    'td mode not in list' => [['td_mode_perps' => 'cash'], 'changes.td_mode_perps'],
    'td mode not a string' => [['td_mode_perps' => 1], 'changes.td_mode_perps'],
    'timeout zero' => [['entry_timeout_minutes' => 0], 'changes.entry_timeout_minutes'],
    'verify too long' => [['stop_verify_seconds' => 61], 'changes.stop_verify_seconds'],
    'retries as string' => [['order_max_retries' => '3'], 'changes.order_max_retries'],
    'atr mult zero' => [['emergency_stop_atr_mult' => '0'], 'changes.emergency_stop_atr_mult'],
]);
