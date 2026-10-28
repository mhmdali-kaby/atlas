<?php

declare(strict_types=1);

use App\Models\User;
use Illuminate\Support\Str;
use Illuminate\Testing\TestResponse;
use Symfony\Component\HttpFoundation\Response;
use Tests\TestCase;

const SPA_ORIGIN = 'http://localhost:3000';

/**
 * Carries the cookies a response set into the next request, like a browser would.
 *
 * @param  TestResponse<Response>  $response
 */
function carryCookies(TestCase $test, TestResponse $response): void
{
    foreach ($response->headers->getCookies() as $cookie) {
        $test->withUnencryptedCookie($cookie->getName(), (string) $cookie->getValue());
    }
}

function makeOwner(string $password = 'correct horse battery staple'): User
{
    return User::factory()->create([
        'name' => 'Owner',
        'email' => 'owner@atlas.test',
        'password' => $password,
        'locale' => 'ar',
        'theme' => 'dark',
    ]);
}

beforeEach(function (): void {
    $this->withHeaders(['Origin' => SPA_ORIGIN, 'Referer' => SPA_ORIGIN.'/', 'Accept' => 'application/json']);
});

it('signs the owner in with the session cookie and returns them from /me', function (): void {
    $owner = makeOwner();

    $csrf = $this->get('/sanctum/csrf-cookie')->assertNoContent();
    expect(collect($csrf->headers->getCookies())->map->getName())->toContain('XSRF-TOKEN');
    carryCookies($this, $csrf);

    $login = $this->postJson('/api/auth/login', [
        'email' => 'owner@atlas.test',
        'password' => 'correct horse battery staple',
    ])->assertOk()
        ->assertHeader('Access-Control-Allow-Origin', SPA_ORIGIN)
        ->assertHeader('Access-Control-Allow-Credentials', 'true');
    carryCookies($this, $login);

    $me = $this->getJson('/api/auth/me')->assertOk()->assertExactJson(['data' => [
        'id' => $owner->id,
        'name' => 'Owner',
        'email' => 'owner@atlas.test',
        'locale' => 'ar',
        'theme' => 'dark',
    ]]);

    expect(Str::isUuid($me->json('data.id')))->toBeTrue()
        ->and($me->json('data'))->not->toHaveKey('password');
});

it('rejects a wrong password with 422 and errors.email', function (): void {
    makeOwner();

    $this->postJson('/api/auth/login', ['email' => 'owner@atlas.test', 'password' => 'wrong'])
        ->assertStatus(422)
        ->assertJsonValidationErrors(['email'])
        ->assertJsonMissingPath('errors.password');
});

it('throttles the sixth login attempt within a minute with 429', function (): void {
    makeOwner();

    foreach (range(1, 5) as $_) {
        $this->postJson('/api/auth/login', ['email' => 'owner@atlas.test', 'password' => 'wrong'])->assertStatus(422);
    }

    $this->postJson('/api/auth/login', ['email' => 'owner@atlas.test', 'password' => 'wrong'])->assertStatus(429);
});

it('returns 401 from /me without a session', function (): void {
    $this->getJson('/api/auth/me')->assertUnauthorized()->assertExactJson(['message' => 'Unauthenticated.']);
});

it('returns a JSON 401, not a redirect, when the client omits Accept: application/json', function (): void {
    $this->withHeaders(['Accept' => '*/*'])->get('/api/auth/me')
        ->assertUnauthorized()
        ->assertExactJson(['message' => 'Unauthenticated.']);
});

it('returns 401 from /me after logout', function (): void {
    makeOwner();
    carryCookies($this, $this->get('/sanctum/csrf-cookie'));

    $login = $this->postJson('/api/auth/login', [
        'email' => 'owner@atlas.test',
        'password' => 'correct horse battery staple',
    ])->assertOk();
    carryCookies($this, $login);
    $this->getJson('/api/auth/me')->assertOk();

    $logout = $this->postJson('/api/auth/logout')->assertNoContent();
    carryCookies($this, $logout);
    $this->app['auth']->forgetGuards();

    $this->getJson('/api/auth/me')->assertUnauthorized();
});

it('sends no CORS allow-origin header to a foreign origin', function (): void {
    $this->withHeaders(['Origin' => 'http://evil.test', 'Referer' => 'http://evil.test/'])
        ->getJson('/api/ping')
        ->assertOk()
        ->assertHeaderMissing('Access-Control-Allow-Origin');

    $this->withHeaders(['Origin' => 'http://evil.test', 'Access-Control-Request-Method' => 'POST'])
        ->options('/api/auth/login')
        ->assertHeaderMissing('Access-Control-Allow-Origin');
});

it('puts security headers on every API response, including errors', function (string $method, string $uri, int $status): void {
    $this->json($method, $uri)
        ->assertStatus($status)
        ->assertHeader('X-Frame-Options', 'DENY')
        ->assertHeader('X-Content-Type-Options', 'nosniff')
        ->assertHeader('Referrer-Policy', 'same-origin')
        ->assertHeader('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
})->with([
    'ok' => ['GET', '/api/ping', 200],
    'unauthenticated' => ['GET', '/api/auth/me', 401],
    'validation' => ['POST', '/api/auth/login', 422],
    'not found' => ['GET', '/api/nope', 404],
]);

it('no longer exposes the install:api /api/user route', function (): void {
    $this->getJson('/api/user')->assertNotFound();
});
