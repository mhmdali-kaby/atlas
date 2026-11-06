<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\User;
use App\Support\TwoFactor\TwoFactor;
use Illuminate\Cookie\CookieValuePrefix;
use Illuminate\Support\Facades\Crypt;
use Illuminate\Testing\TestResponse;
use PragmaRX\Google2FA\Google2FA;
use Symfony\Component\HttpFoundation\Response;
use Tests\TestCase;

/**
 * ATL-246, decision #265 (1): sign-in asks for the TOTP or a recovery code when 2FA is on.
 */
const CHALLENGE_PASSWORD = 'correct horse battery staple';

/** @param  TestResponse<Response>  $response */
function carryChallengeCookies(TestCase $test, TestResponse $response): void
{
    foreach ($response->headers->getCookies() as $cookie) {
        $test->withUnencryptedCookie($cookie->getName(), (string) $cookie->getValue());
    }
}

/**
 * The session id a response's session cookie carries.
 *
 * @param  TestResponse<Response>  $response
 */
function challengeSessionId(TestResponse $response): string
{
    $name = (string) config('session.cookie');
    foreach ($response->headers->getCookies() as $cookie) {
        if ($cookie->getName() === $name) {
            return CookieValuePrefix::remove(Crypt::decryptString((string) $cookie->getValue(), false));
        }
    }
    throw new RuntimeException('no session cookie');
}

/** Turns 2FA on for $user directly through the service; returns [secret, recovery codes]. */
function enableChallengeTwoFactor(User $user): array
{
    $service = app(TwoFactor::class);
    $secret = $service->startEnrolment($user);
    $codes = $service->confirm($user);

    return [$secret, $codes];
}

/** A 6-digit code that is not the current one (nor either neighbour in the window). */
function wrongChallengeCode(string $secret): string
{
    $g = new Google2FA;
    $candidate = 0;
    while ($g->verifyKey($secret, str_pad((string) $candidate, 6, '0', STR_PAD_LEFT), 2)) {
        $candidate++;
    }

    return str_pad((string) $candidate, 6, '0', STR_PAD_LEFT);
}

/** POSTs the correct password with cookies carried, and returns the response. */
function passwordStep(TestCase $test): TestResponse
{
    $response = $test->postJson('/api/auth/login', [
        'email' => 'owner@atlas.test',
        'password' => CHALLENGE_PASSWORD,
    ]);
    carryChallengeCookies($test, $response);

    return $response;
}

beforeEach(function (): void {
    $this->withCredentials();
    $this->withHeaders(['Origin' => 'http://localhost:3000', 'Referer' => 'http://localhost:3000/', 'Accept' => 'application/json']);
    $this->owner = User::factory()->create(['email' => 'owner@atlas.test', 'password' => CHALLENGE_PASSWORD]);
    carryChallengeCookies($this, $this->get('/sanctum/csrf-cookie'));
});

it('answers a correct password with two_factor=true and does not sign in', function (): void {
    enableChallengeTwoFactor($this->owner);

    passwordStep($this)->assertOk()->assertExactJson(['two_factor' => true]);

    $this->getJson('/api/auth/me')->assertUnauthorized();
    $this->getJson('/api/auth/status')->assertOk()
        ->assertExactJson(['owner_exists' => true, 'two_factor_pending' => true]);
});

it('still refuses a wrong password with 422 and no pending challenge', function (): void {
    enableChallengeTwoFactor($this->owner);

    $this->postJson('/api/auth/login', ['email' => 'owner@atlas.test', 'password' => 'wrong'])
        ->assertStatus(422)->assertJsonValidationErrors(['email']);
    $this->getJson('/api/auth/status')->assertJsonPath('two_factor_pending', false);
});

it('signs in with a valid code and changes the session id', function (): void {
    [$secret] = enableChallengeTwoFactor($this->owner);
    $before = challengeSessionId(passwordStep($this));

    $challenge = $this->postJson('/api/auth/two-factor-challenge', ['code' => (new Google2FA)->getCurrentOtp($secret)])
        ->assertOk()
        ->assertJsonPath('data.id', $this->owner->id)
        ->assertJsonPath('data.email', 'owner@atlas.test');
    carryChallengeCookies($this, $challenge);

    expect(challengeSessionId($challenge))->not->toBe($before);
    $this->getJson('/api/auth/me')->assertOk()->assertJsonPath('data.id', $this->owner->id);
    $this->getJson('/api/auth/status')->assertJsonPath('two_factor_pending', false);

    $row = AuditLog::query()->where('action', 'auth.two_factor_challenge.passed')->sole();
    expect($row->subject_id)->toBe($this->owner->id)
        ->and($row->after)->toBe(['method' => 'code']);
});

it('refuses a wrong code with 422 and stays signed out', function (): void {
    [$secret] = enableChallengeTwoFactor($this->owner);
    passwordStep($this);

    $this->postJson('/api/auth/two-factor-challenge', ['code' => wrongChallengeCode($secret)])
        ->assertStatus(422)
        ->assertJsonPath('error', 'code_wrong')
        ->assertJsonValidationErrors(['code']);

    $this->getJson('/api/auth/me')->assertUnauthorized();
    // The challenge is still open: the owner can try again.
    $this->getJson('/api/auth/status')->assertJsonPath('two_factor_pending', true);

    $row = AuditLog::query()->where('action', 'auth.two_factor_challenge.failed')->sole();
    expect($row->after)->toBe(['method' => 'code', 'reason' => 'wrong_code']);
    expect(json_encode(AuditLog::query()->get()->toArray()))->not->toContain($secret);
});

it('accepts a recovery code once and refuses it the second time', function (): void {
    [, $codes] = enableChallengeTwoFactor($this->owner);
    passwordStep($this);

    $first = $this->postJson('/api/auth/two-factor-challenge', ['recovery_code' => $codes[0]])->assertOk();
    carryChallengeCookies($this, $first);
    $this->getJson('/api/auth/me')->assertOk();

    $logout = $this->postJson('/api/auth/logout')->assertNoContent();
    carryChallengeCookies($this, $logout);
    $this->app['auth']->forgetGuards();
    carryChallengeCookies($this, $this->get('/sanctum/csrf-cookie'));

    passwordStep($this)->assertExactJson(['two_factor' => true]);
    $this->postJson('/api/auth/two-factor-challenge', ['recovery_code' => $codes[0]])
        ->assertStatus(422)
        ->assertJsonValidationErrors(['recovery_code']);
    $this->getJson('/api/auth/me')->assertUnauthorized();

    $passed = AuditLog::query()->where('action', 'auth.two_factor_challenge.passed')->sole();
    expect($passed->after)->toBe(['method' => 'recovery_code', 'recovery_codes_left' => 7]);
    expect(json_encode(AuditLog::query()->get()->toArray()))->not->toContain($codes[0]);
});

it('refuses the challenge after 5 minutes and asks to sign in again', function (): void {
    [$secret] = enableChallengeTwoFactor($this->owner);
    passwordStep($this);

    $this->travel(5)->minutes();
    $this->travel(1)->seconds();

    $this->postJson('/api/auth/two-factor-challenge', ['code' => (new Google2FA)->getCurrentOtp($secret)])
        ->assertStatus(422)
        ->assertJsonPath('error', 'challenge_expired')
        ->assertJsonPath('message', __('two_factor.challenge_expired'));
    $this->getJson('/api/auth/me')->assertUnauthorized();
    $this->getJson('/api/auth/status')->assertJsonPath('two_factor_pending', false);

    expect(AuditLog::query()->where('action', 'auth.two_factor_challenge.failed')->sole()->after)
        ->toBe(['method' => 'code', 'reason' => 'expired']);
});

it('refuses the challenge with no password step at all', function (): void {
    enableChallengeTwoFactor($this->owner);

    $this->postJson('/api/auth/two-factor-challenge', ['code' => '123456'])
        ->assertStatus(422)
        ->assertJsonPath('error', 'challenge_expired');
    $this->getJson('/api/auth/me')->assertUnauthorized();
});

it('throttles the sixth challenge attempt within a minute with 429', function (): void {
    [$secret] = enableChallengeTwoFactor($this->owner);
    passwordStep($this);
    $wrong = wrongChallengeCode($secret);

    foreach (range(1, 5) as $_) {
        $this->postJson('/api/auth/two-factor-challenge', ['code' => $wrong])->assertStatus(422);
    }

    $this->postJson('/api/auth/two-factor-challenge', ['code' => (new Google2FA)->getCurrentOtp($secret)])
        ->assertStatus(429);
    $this->getJson('/api/auth/me')->assertUnauthorized();
});

it('requires a code or a recovery code', function (): void {
    enableChallengeTwoFactor($this->owner);
    passwordStep($this);

    $this->postJson('/api/auth/two-factor-challenge', [])
        ->assertStatus(422)
        ->assertJsonValidationErrors(['code', 'recovery_code']);
});

it('does not treat an unconfirmed setup as 2FA on: the password signs in in one step', function (): void {
    app(TwoFactor::class)->startEnrolment($this->owner);

    $login = passwordStep($this)->assertOk()->assertJsonPath('data.id', $this->owner->id);
    expect($login->json())->not->toHaveKey('two_factor');
    $this->getJson('/api/auth/me')->assertOk();
});

it('refuses a pending challenge once 2FA has been turned off meanwhile', function (): void {
    [$secret] = enableChallengeTwoFactor($this->owner);
    passwordStep($this);

    app(TwoFactor::class)->disable($this->owner->refresh());

    $this->postJson('/api/auth/two-factor-challenge', ['code' => (new Google2FA)->getCurrentOtp($secret)])
        ->assertStatus(422)
        ->assertJsonPath('error', 'challenge_expired');
    $this->getJson('/api/auth/me')->assertUnauthorized();
});
