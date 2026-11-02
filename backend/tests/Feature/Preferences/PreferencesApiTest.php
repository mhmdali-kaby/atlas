<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\User;

/**
 * ATL-226, decision #255 (1): GET/PATCH /api/me/preferences.
 */
beforeEach(function (): void {
    $this->user = User::factory()->create(['name' => 'Owner']);
});

it('returns the defaults for a fresh owner', function (): void {
    $this->actingAs($this->user)->getJson('/api/me/preferences')
        ->assertOk()
        ->assertExactJson(['data' => [
            'name' => 'Owner',
            'locale' => 'en',
            'timezone' => 'Asia/Baghdad',
            'theme' => 'system',
        ]]);
});

it('refuses an unauthenticated read and write', function (): void {
    $this->getJson('/api/me/preferences')->assertUnauthorized();
    $this->patchJson('/api/me/preferences', ['theme' => 'dark'])->assertUnauthorized();
});

it('saves every preference and returns it on the next read', function (): void {
    $body = ['locale' => 'ar', 'timezone' => 'Europe/London', 'theme' => 'dark', 'name' => 'Mohammed'];

    $this->actingAs($this->user)->patchJson('/api/me/preferences', $body)
        ->assertOk()
        ->assertExactJson(['data' => ['name' => 'Mohammed', 'locale' => 'ar', 'timezone' => 'Europe/London', 'theme' => 'dark']]);

    $this->actingAs($this->user->fresh())->getJson('/api/me/preferences')
        ->assertOk()
        ->assertExactJson(['data' => ['name' => 'Mohammed', 'locale' => 'ar', 'timezone' => 'Europe/London', 'theme' => 'dark']]);
});

it('changes only the fields that are sent', function (): void {
    $this->actingAs($this->user)->patchJson('/api/me/preferences', ['theme' => 'light'])
        ->assertOk()
        ->assertJsonPath('data.theme', 'light')
        ->assertJsonPath('data.locale', 'en')
        ->assertJsonPath('data.timezone', 'Asia/Baghdad')
        ->assertJsonPath('data.name', 'Owner');
});

it('rejects an unknown timezone, locale and theme, naming each field', function (): void {
    $this->actingAs($this->user)
        ->patchJson('/api/me/preferences', ['timezone' => 'Mars/Base', 'locale' => 'fr', 'theme' => 'blue'])
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['timezone', 'locale', 'theme'])
        ->assertJsonPath('errors.locale.0', 'The selected language is invalid.')
        ->assertJsonPath('errors.theme.0', 'The selected appearance is invalid.')
        ->assertJsonPath('errors.timezone.0', 'The time zone must be a valid IANA time zone, such as Asia/Baghdad.');

    expect($this->user->fresh()->only(['locale', 'timezone', 'theme']))
        ->toBe(['locale' => 'en', 'timezone' => 'Asia/Baghdad', 'theme' => 'system']);
    expect(AuditLog::query()->where('action', 'preferences.updated')->count())->toBe(0);
});

it('returns the validation messages in Arabic for Accept-Language: ar', function (): void {
    $this->actingAs($this->user)
        ->withHeaders(['Accept-Language' => 'ar'])
        ->patchJson('/api/me/preferences', ['timezone' => 'Mars/Base', 'locale' => 'fr', 'theme' => 'blue', 'name' => ''])
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['timezone', 'locale', 'theme', 'name'])
        ->assertJsonPath('errors.locale.0', 'القيمة المحددة لـ اللغة غير صالحة.')
        ->assertJsonPath('errors.theme.0', 'القيمة المحددة لـ المظهر غير صالحة.')
        ->assertJsonPath('errors.timezone.0', 'يجب أن تكون المنطقة الزمنية منطقة IANA صالحة، مثل Asia/Baghdad.')
        ->assertJsonPath('errors.name.0', fn (string $m): bool => str_contains($m, 'الاسم'));
});

it('rejects a name that is empty, blank or longer than 80 characters', function (string $name): void {
    $this->actingAs($this->user)->patchJson('/api/me/preferences', ['name' => $name])
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['name']);
})->with(['empty' => '', 'blank' => '   ', 'too long' => str_repeat('a', 81)]);

it('accepts an 80-character name', function (): void {
    $this->actingAs($this->user)->patchJson('/api/me/preferences', ['name' => str_repeat('a', 80)])
        ->assertOk()
        ->assertJsonPath('data.name', str_repeat('a', 80));
});

it('writes one audit row per PATCH with the old and new value of each changed field', function (): void {
    $this->actingAs($this->user)->patchJson('/api/me/preferences', [
        'locale' => 'ar', 'timezone' => 'Europe/London', 'theme' => 'system', 'name' => 'Mohammed',
    ])->assertOk();

    $rows = AuditLog::query()->where('action', 'preferences.updated')->get();
    expect($rows)->toHaveCount(1);
    $row = $rows->first();
    expect($row->user_id)->toBe($this->user->id)
        ->and($row->subject_type)->toBe('users')
        ->and($row->subject_id)->toBe($this->user->id)
        // theme was already "system", so it is not a change.
        ->and($row->before)->toBe(['name' => 'Owner', 'locale' => 'en', 'timezone' => 'Asia/Baghdad'])
        ->and($row->after)->toBe(['name' => 'Mohammed', 'locale' => 'ar', 'timezone' => 'Europe/London']);

    $this->actingAs($this->user->fresh())->patchJson('/api/me/preferences', ['theme' => 'dark'])->assertOk();
    $latest = AuditLog::query()->where('action', 'preferences.updated')->orderByDesc('created_at')->get();
    expect($latest)->toHaveCount(2)
        ->and(AuditLog::query()->where('action', 'preferences.updated')->whereJsonContains('after->theme', 'dark')->first()?->before)
        ->toBe(['theme' => 'system']);
});

it('does not touch the scheduling timezone', function (): void {
    $this->actingAs($this->user)->patchJson('/api/me/preferences', ['timezone' => 'Europe/London'])->assertOk();

    expect(config('atlas_defaults.timezone.display'))->toBe('Asia/Baghdad');
});
