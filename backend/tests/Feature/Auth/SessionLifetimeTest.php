<?php

declare(strict_types=1);

use App\Models\User;
use Illuminate\Auth\SessionGuard;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

/*
 * ATL-32 (security round 1, low severity): the shipped server session lifetime is 15 minutes, so
 * a closed or crashed tab (which never runs the SPA's own 15-minute idle-lock logout) can't leave
 * a 2-hour session cookie valid. This exercises the real DatabaseSessionHandler expiry, which is
 * what actually enforces it: session.driver must be "database" for a stale row to be refused on
 * read (the "array" driver phpunit.xml uses for ordinary tests never expires anything).
 *
 * The session row is inserted directly rather than produced through a login HTTP call: a login
 * flow followed by a second $this->getJson() call in the same test doesn't exercise storage at
 * all (the "web" guard caches the user it resolved on the first call, in the same Application
 * instance, independently of any cookie). Inserting the row and reading it with exactly one real
 * request has no such artifact, so it also needs withCredentials() (off by default for *Json()
 * calls — see MakesHttpRequests::prepareCookiesForJsonRequest) and the regular, encrypted
 * withCookie() (not withUnencryptedCookie(), which skips server-side decryption entirely and
 * would be read as an invalid, freshly-replaced session id).
 */
function putAtl32SessionRow(string $userId, int $minutesAgo): string
{
    $sessionId = Str::random(40);
    $loginKey = 'login_web_'.sha1(SessionGuard::class);
    $json = json_encode([$loginKey => $userId, '_token' => Str::random(40)], JSON_THROW_ON_ERROR);

    DB::table('sessions')->insert([
        'id' => $sessionId,
        'user_id' => $userId,
        'ip_address' => '127.0.0.1',
        'user_agent' => 'Atlas test',
        // DatabaseSessionHandler::read() always base64_decode()s the payload column.
        'payload' => base64_encode($json),
        'last_activity' => now()->subMinutes($minutesAgo)->getTimestamp(),
    ]);

    return $sessionId;
}

beforeEach(function (): void {
    config(['session.driver' => 'database', 'session.lifetime' => 15]);
    $this->withCredentials();
    // Without Origin/Referer, EnsureFrontendRequestsAreStateful::fromFrontend() is false and the
    // whole frontend middleware group (StartSession included) is skipped for the request.
    $this->withHeaders(['Origin' => 'http://localhost:3000', 'Referer' => 'http://localhost:3000/', 'Accept' => 'application/json']);
});

it('returns 401 from /api/auth/me once the session has been idle past the shipped 15-minute lifetime', function (): void {
    $owner = User::factory()->create();
    $sessionId = putAtl32SessionRow($owner->id, minutesAgo: 16);

    $this->withCookie((string) config('session.cookie'), $sessionId)
        ->getJson('/api/auth/me')
        ->assertUnauthorized();
});

it('still accepts a session active 10 minutes ago, inside the 15-minute lifetime', function (): void {
    $owner = User::factory()->create();
    $sessionId = putAtl32SessionRow($owner->id, minutesAgo: 10);

    $this->withCookie((string) config('session.cookie'), $sessionId)
        ->getJson('/api/auth/me')
        ->assertOk()
        ->assertJsonPath('data.id', $owner->id);
});
