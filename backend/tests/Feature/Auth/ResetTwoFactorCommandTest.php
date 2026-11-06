<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\User;
use App\Support\TwoFactor\TwoFactor;
use Illuminate\Support\Facades\Artisan;

/**
 * ATL-255, decision #265 (1): `./atlas reset-two-factor` for an owner who lost both their phone
 * and every recovery code.
 */
const RESET_PASSWORD = 'correct horse battery staple';

/** Turns 2FA on for $user directly through the service; returns [secret, recovery codes]. */
function enableResetTwoFactor(User $user): array
{
    $service = app(TwoFactor::class);
    $secret = $service->startEnrolment($user);
    $codes = $service->confirm($user);

    return [$secret, $codes];
}

beforeEach(function (): void {
    $this->withHeaders(['Origin' => 'http://localhost:3000', 'Referer' => 'http://localhost:3000/', 'Accept' => 'application/json']);
    $this->owner = User::factory()->create(['email' => 'owner@atlas.test', 'password' => RESET_PASSWORD]);
});

it('turns 2FA off with --force, and the owner then signs in with just the password', function (): void {
    enableResetTwoFactor($this->owner);

    $this->artisan('atlas:reset-two-factor', ['--force' => true])->assertSuccessful();

    expect($this->owner->fresh())
        ->two_factor_secret->toBeNull()
        ->two_factor_recovery_codes->toBeNull()
        ->two_factor_confirmed_at->toBeNull()
        ->two_factor_last_timestep->toBeNull();

    $this->postJson('/api/auth/login', ['email' => 'owner@atlas.test', 'password' => RESET_PASSWORD])
        ->assertOk()
        ->assertJsonPath('data.id', $this->owner->id)
        ->assertJsonMissingPath('two_factor');
});

it('leaves 2FA on and exits non-zero when the owner answers no', function (): void {
    enableResetTwoFactor($this->owner);

    $this->artisan('atlas:reset-two-factor')
        ->expectsConfirmation(
            'Turn off two-factor sign-in for owner@atlas.test? This removes the authenticator and every recovery code.',
            'no',
        )
        ->assertFailed();

    expect($this->owner->fresh()->two_factor_confirmed_at)->not->toBeNull();
    expect(AuditLog::query()->where('action', 'two_factor.reset')->count())->toBe(0);
});

it('writes one audit row with no secret or recovery code in the output', function (): void {
    [$secret, $codes] = enableResetTwoFactor($this->owner);

    $code = Artisan::call('atlas:reset-two-factor', ['--force' => true]);
    $output = Artisan::output();
    expect($code)->toBe(0);

    $row = AuditLog::query()->where('action', 'two_factor.reset')->sole();
    expect($row->subject_id)->toBe($this->owner->id)
        ->and($row->user_id)->toBe($this->owner->id)
        ->and($row->after)->toBe(['actor' => 'cli', 'enabled' => false]);

    expect($output)->not->toContain($secret);
    foreach ($codes as $code) {
        expect($output)->not->toContain($code);
    }
});

it('says 2FA is already off and writes no audit row', function (): void {
    $this->artisan('atlas:reset-two-factor', ['--force' => true])
        ->expectsOutputToContain('already off')
        ->assertSuccessful();

    expect(AuditLog::query()->where('action', 'two_factor.reset')->count())->toBe(0);
});

it('resolves the single owner without --email, and refuses an unknown email', function (): void {
    enableResetTwoFactor($this->owner);

    $this->artisan('atlas:reset-two-factor', ['--email' => 'nobody@atlas.test', '--force' => true])
        ->assertFailed();
    expect($this->owner->fresh()->two_factor_confirmed_at)->not->toBeNull();

    $this->artisan('atlas:reset-two-factor', ['--email' => 'OWNER@atlas.test', '--force' => true])
        ->assertSuccessful();
    expect($this->owner->fresh()->two_factor_confirmed_at)->toBeNull();
});

it('refuses with more than one user and no --email', function (): void {
    User::factory()->create(['email' => 'second@atlas.test']);

    $this->artisan('atlas:reset-two-factor', ['--force' => true])->assertFailed();
});
