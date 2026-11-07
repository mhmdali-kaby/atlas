<?php

declare(strict_types=1);

use App\Models\Setting;
use App\Models\SettingsHistory;
use App\Models\User;

/** ATL-212, decision #254: autonomy 3-4 and the autonomy_ask_above_usd settings key. */
beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
});

it('defaults autonomy_ask_above_usd to 1000 and enables levels up to 4', function (): void {
    $this->getJson('/api/settings?env=demo')
        ->assertOk()
        ->assertJsonPath('data.settings.autonomy_ask_above_usd', '1000')
        ->assertJsonPath('data.max_enabled_autonomy_level', 4);
});

it('accepts autonomy 3 and 4 with an ask-above amount and versions each change', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $historyBefore = SettingsHistory::query()->count();

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => [
        'autonomy_level' => 3,
        'autonomy_ask_above_usd' => '250.50',
    ]])
        ->assertOk()
        ->assertJsonPath('data.version', 2)
        ->assertJsonPath('data.settings.autonomy_level', 3)
        ->assertJsonPath('data.settings.autonomy_ask_above_usd', '250.50');

    $history = SettingsHistory::query()->latest('version')->firstOrFail();
    expect($history->version)->toBe(2)
        ->and($history->diff)->toEqual([
            'autonomy_level' => ['from' => 2, 'to' => 3],
            'autonomy_ask_above_usd' => ['from' => '1000', 'to' => '250.50'],
        ])
        ->and($history->data['autonomy_ask_above_usd'])->toBe('250.50');

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 2, 'changes' => ['autonomy_level' => 4]])
        ->assertOk()
        ->assertJsonPath('data.version', 3)
        ->assertJsonPath('data.settings.autonomy_level', 4);

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 3, 'changes' => ['autonomy_ask_above_usd' => '0']])
        ->assertOk()
        ->assertJsonPath('data.settings.autonomy_ask_above_usd', '0');

    expect(SettingsHistory::query()->count())->toBe($historyBefore + 3)
        ->and(Setting::query()->where('env', 'demo')->value('autonomy_level'))->toBe(4);
});

it('refuses an invalid ask-above amount with 422 and writes no version', function (mixed $value): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $historyBefore = SettingsHistory::query()->count();

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['autonomy_ask_above_usd' => $value]])
        ->assertUnprocessable()
        ->assertJsonValidationErrors('changes.autonomy_ask_above_usd');

    expect(Setting::query()->where('env', 'demo')->value('version'))->toBe(1)
        ->and(SettingsHistory::query()->count())->toBe($historyBefore);
})->with([
    'negative' => ['-1'],
    'negative number' => [-1],
    'non-numeric' => ['abc'],
    'float' => [250.5],
    'null' => [null],
    'empty' => [''],
]);

/** ATL-222 (QA on ATL-216): the message must name the field in plain words, not its raw key. */
it('names the field in plain English for a negative ask-above amount', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();

    $response = $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['autonomy_ask_above_usd' => '-1']])
        ->assertUnprocessable()
        ->assertJsonValidationErrors('changes.autonomy_ask_above_usd');

    $message = $response->json('errors')['changes.autonomy_ask_above_usd'][0] ?? null;
    expect($message)->toBeString()
        ->and($message)->not->toContain('autonomy_ask_above_usd')
        ->and($message)->not->toContain('_')
        ->and($message)->toContain('Ask me above');
});

it('names the field in plain Arabic for a negative ask-above amount', function (): void {
    $this->withHeaders(['Accept-Language' => 'ar'])->getJson('/api/settings?env=demo')->assertOk();

    $response = $this->withHeaders(['Accept-Language' => 'ar'])
        ->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['autonomy_ask_above_usd' => '-1']])
        ->assertUnprocessable()
        ->assertJsonValidationErrors('changes.autonomy_ask_above_usd');

    $message = $response->json('errors')['changes.autonomy_ask_above_usd'][0] ?? null;
    expect($message)->toBeString()
        ->and($message)->not->toContain('autonomy_ask_above_usd')
        ->and($message)->not->toContain('_')
        ->and($message)->toContain('اسألني فوق');
});
