<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\User;
use App\Support\TwoFactor\TwoFactor;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use PragmaRX\Google2FA\Google2FA;

/**
 * ATL-245, decision #265 (1): /api/me/two-factor enrolment and the TwoFactor service.
 */
beforeEach(function (): void {
    $this->user = User::factory()->create(['password' => 'correct horse battery']);
});

/** Starts enrolment for $user and returns the plaintext secret. */
function startTwoFactor(User $user): string
{
    return test()->actingAs($user)
        ->postJson('/api/me/two-factor/start', ['password' => 'correct horse battery'])
        ->assertOk()
        ->json('data.secret');
}

/** Starts and confirms enrolment for $user; returns [secret, recovery codes]. */
function enableTwoFactor(User $user): array
{
    $secret = startTwoFactor($user);
    $codes = test()->actingAs($user)
        ->postJson('/api/me/two-factor/confirm', ['code' => (new Google2FA)->getCurrentOtp($secret)])
        ->assertOk()
        ->json('data.recovery_codes');

    return [$secret, $codes];
}

/** The raw (still encrypted) column values for $user. */
function rawTwoFactorRow(User $user): object
{
    return DB::table('users')->where('id', $user->id)->first([
        'two_factor_secret', 'two_factor_recovery_codes', 'two_factor_confirmed_at', 'two_factor_last_timestep',
    ]);
}

it('refuses every route when signed out', function (): void {
    $this->getJson('/api/me/two-factor')->assertUnauthorized();
    $this->postJson('/api/me/two-factor/start', ['password' => 'x'])->assertUnauthorized();
    $this->postJson('/api/me/two-factor/confirm', ['code' => '123456'])->assertUnauthorized();
    $this->postJson('/api/me/two-factor/recovery-codes', ['password' => 'x'])->assertUnauthorized();
    $this->deleteJson('/api/me/two-factor', ['password' => 'x'])->assertUnauthorized();
});

it('reports 2FA off for a fresh owner', function (): void {
    $this->actingAs($this->user)->getJson('/api/me/two-factor')
        ->assertOk()
        ->assertExactJson(['data' => ['enabled' => false, 'confirmed_at' => null, 'recovery_codes_left' => 0]]);
});

it('refuses start with a wrong password and stores nothing', function (): void {
    $this->actingAs($this->user)
        ->postJson('/api/me/two-factor/start', ['password' => 'wrong'])
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['password'])
        ->assertJsonPath('errors.password.0', 'The password is incorrect.');

    expect(rawTwoFactorRow($this->user)->two_factor_secret)->toBeNull()
        ->and(AuditLog::query()->where('action', 'like', 'two_factor.%')->count())->toBe(0);
});

it('starts with the right password: an SVG and a base32 secret, still disabled', function (): void {
    $response = $this->actingAs($this->user)
        ->postJson('/api/me/two-factor/start', ['password' => 'correct horse battery'])
        ->assertOk();

    expect($response->json('data.qr_svg'))->toContain('<svg')
        ->and($response->json('data.secret'))->toMatch('/^[A-Z2-7]{32}$/');

    $this->actingAs($this->user->fresh())->getJson('/api/me/two-factor')
        ->assertOk()
        ->assertJsonPath('data.enabled', false)
        ->assertJsonPath('data.confirmed_at', null);
});

it('replaces an earlier unconfirmed secret on a second start', function (): void {
    $first = startTwoFactor($this->user);
    $second = startTwoFactor($this->user->fresh());

    expect($second)->not->toBe($first)
        ->and($this->user->fresh()->two_factor_secret)->toBe($second);

    // A code from the abandoned secret no longer confirms.
    $this->actingAs($this->user->fresh())
        ->postJson('/api/me/two-factor/confirm', ['code' => (new Google2FA)->getCurrentOtp($first)])
        ->assertUnprocessable();
});

it('confirms with a code from the secret: enabled and exactly 8 recovery codes', function (): void {
    $secret = startTwoFactor($this->user);

    $response = $this->actingAs($this->user->fresh())
        ->postJson('/api/me/two-factor/confirm', ['code' => (new Google2FA)->getCurrentOtp($secret)])
        ->assertOk()
        ->assertJsonPath('data.enabled', true)
        ->assertJsonPath('data.recovery_codes_left', 8);

    $codes = $response->json('data.recovery_codes');
    expect($codes)->toHaveCount(8)
        ->and(array_unique($codes))->toHaveCount(8)
        ->and($response->json('data'))->not->toHaveKey('secret');

    $this->actingAs($this->user->fresh())->getJson('/api/me/two-factor')
        ->assertOk()
        ->assertJsonPath('data.enabled', true)
        ->assertJsonPath('data.recovery_codes_left', 8);
    expect($this->user->fresh()->two_factor_confirmed_at)->not->toBeNull();
});

it('refuses a wrong code with 422 and stays disabled', function (): void {
    $secret = startTwoFactor($this->user);
    $right = (new Google2FA)->getCurrentOtp($secret);
    $wrong = str_pad((string) (((int) $right + 1) % 1000000), 6, '0', STR_PAD_LEFT);

    $this->actingAs($this->user->fresh())
        ->postJson('/api/me/two-factor/confirm', ['code' => $wrong])
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['code']);

    $this->actingAs($this->user->fresh())->getJson('/api/me/two-factor')
        ->assertJsonPath('data.enabled', false);
    expect($this->user->fresh()->two_factor_confirmed_at)->toBeNull();
});

it('refuses confirm before start, and start once 2FA is on (the secret is never shown again)', function (): void {
    $this->actingAs($this->user)
        ->postJson('/api/me/two-factor/confirm', ['code' => '123456'])
        ->assertStatus(409)
        ->assertJsonPath('error', 'not_started');

    enableTwoFactor($this->user->fresh());

    $this->actingAs($this->user->fresh())
        ->postJson('/api/me/two-factor/start', ['password' => 'correct horse battery'])
        ->assertStatus(409)
        ->assertJsonPath('error', 'already_enabled')
        ->assertJsonMissingPath('data.secret');
});

it('stores the secret and recovery codes as ciphertext, and status never shows them', function (): void {
    [$secret, $codes] = enableTwoFactor($this->user);
    $raw = rawTwoFactorRow($this->user);

    expect($raw->two_factor_secret)->not->toBeNull()
        ->and($raw->two_factor_secret)->not->toContain($secret)
        ->and(decrypt($raw->two_factor_secret, false))->toBe($secret);

    $stored = json_decode(decrypt($raw->two_factor_recovery_codes, false), true);
    expect($stored)->toHaveCount(8);
    foreach ($codes as $i => $code) {
        expect($raw->two_factor_recovery_codes)->not->toContain($code)
            ->and(json_encode($stored))->not->toContain($code)
            ->and(Hash::check($code, $stored[$i]))->toBeTrue();
    }

    $body = $this->actingAs($this->user->fresh())->getJson('/api/me/two-factor')->assertOk()->getContent();
    expect($body)->not->toContain($secret);
    foreach ($codes as $code) {
        expect($body)->not->toContain($code);
    }

    // The serialised model hides the columns too.
    expect(json_encode($this->user->fresh()->toArray()))->not->toContain('two_factor_secret')
        ->and(json_encode($this->user->fresh()->toArray()))->not->toContain('two_factor_recovery_codes');
});

it('rejects the same code twice in one timestep', function (): void {
    $secret = startTwoFactor($this->user);
    $service = app(TwoFactor::class);
    $code = (new Google2FA)->getCurrentOtp($secret);
    $user = $this->user->fresh();

    expect($service->verifyCode($user, $code))->toBeTrue()
        ->and($service->verifyCode($user, $code))->toBeFalse()
        ->and($service->verifyCode($user->fresh(), $code))->toBeFalse()
        ->and($service->verifyCode($user, 'abcdef'))->toBeFalse()
        ->and($user->fresh()->two_factor_last_timestep)->toBeInt();
});

it('refuses on confirm a code that was already used', function (): void {
    $secret = startTwoFactor($this->user);
    $code = (new Google2FA)->getCurrentOtp($secret);
    app(TwoFactor::class)->verifyCode($this->user->fresh(), $code);

    $this->actingAs($this->user->fresh())
        ->postJson('/api/me/two-factor/confirm', ['code' => $code])
        ->assertUnprocessable();
});

it('accepts each recovery code exactly once', function (): void {
    [, $codes] = enableTwoFactor($this->user);
    $service = app(TwoFactor::class);
    $user = $this->user->fresh();

    foreach ($codes as $i => $code) {
        expect($service->consumeRecoveryCode($user, $code))->toBeTrue()
            ->and($service->recoveryCodesLeft($user->fresh()))->toBe(7 - $i);
    }
    foreach ($codes as $code) {
        expect($service->consumeRecoveryCode($user->fresh(), $code))->toBeFalse();
    }
    expect($service->consumeRecoveryCode($user->fresh(), 'not-a-code'))->toBeFalse();
});

it('regenerate replaces all 8 codes; the old ones stop working', function (): void {
    [, $old] = enableTwoFactor($this->user);

    $this->actingAs($this->user->fresh())
        ->postJson('/api/me/two-factor/recovery-codes', ['password' => 'wrong'])
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['password']);

    $new = $this->actingAs($this->user->fresh())
        ->postJson('/api/me/two-factor/recovery-codes', ['password' => 'correct horse battery'])
        ->assertOk()
        ->assertJsonPath('data.recovery_codes_left', 8)
        ->json('data.recovery_codes');

    expect($new)->toHaveCount(8)->and(array_intersect($new, $old))->toBe([]);

    $service = app(TwoFactor::class);
    expect($service->consumeRecoveryCode($this->user->fresh(), $old[0]))->toBeFalse()
        ->and($service->consumeRecoveryCode($this->user->fresh(), $new[0]))->toBeTrue();
});

it('refuses regenerate while 2FA is off', function (): void {
    $this->actingAs($this->user)
        ->postJson('/api/me/two-factor/recovery-codes', ['password' => 'correct horse battery'])
        ->assertStatus(409)
        ->assertJsonPath('error', 'not_enabled');
});

it('DELETE needs the password and then clears all four columns', function (): void {
    enableTwoFactor($this->user);

    $this->actingAs($this->user->fresh())
        ->deleteJson('/api/me/two-factor', ['password' => 'wrong'])
        ->assertUnprocessable();
    expect($this->user->fresh()->two_factor_confirmed_at)->not->toBeNull();

    $this->actingAs($this->user->fresh())
        ->deleteJson('/api/me/two-factor', ['password' => 'correct horse battery'])
        ->assertOk()
        ->assertExactJson(['data' => ['enabled' => false, 'confirmed_at' => null, 'recovery_codes_left' => 0]]);

    $raw = rawTwoFactorRow($this->user);
    expect($raw->two_factor_secret)->toBeNull()
        ->and($raw->two_factor_recovery_codes)->toBeNull()
        ->and($raw->two_factor_confirmed_at)->toBeNull()
        ->and($raw->two_factor_last_timestep)->toBeNull();
});

it('writes one audit row per write with no secret or code in it', function (): void {
    [$secret, $codes] = enableTwoFactor($this->user);
    $regenerated = $this->actingAs($this->user->fresh())
        ->postJson('/api/me/two-factor/recovery-codes', ['password' => 'correct horse battery'])
        ->json('data.recovery_codes');
    $this->actingAs($this->user->fresh())
        ->deleteJson('/api/me/two-factor', ['password' => 'correct horse battery'])
        ->assertOk();

    $rows = AuditLog::query()->where('action', 'like', 'two_factor.%')->orderBy('created_at')->get();
    expect($rows->pluck('action')->all())->toBe([
        'two_factor.started',
        'two_factor.confirmed',
        'two_factor.recovery_codes_regenerated',
        'two_factor.disabled',
    ]);

    $dump = json_encode(DB::table('audit_log')->get()->all());
    expect($dump)->not->toContain($secret)->and($dump)->not->toContain('correct horse battery');
    foreach ([...$codes, ...$regenerated] as $code) {
        expect($dump)->not->toContain($code);
    }
    foreach ($rows as $row) {
        expect($row->user_id)->toBe($this->user->id)->and($row->subject_id)->toBe($this->user->id);
    }
});

it('limits enrolment writes to 5 a minute', function (): void {
    for ($i = 0; $i < 5; $i++) {
        $this->actingAs($this->user)->postJson('/api/me/two-factor/start', ['password' => 'wrong'])->assertUnprocessable();
    }
    $this->actingAs($this->user)->postJson('/api/me/two-factor/start', ['password' => 'correct horse battery'])
        ->assertStatus(429);
});
