<?php

declare(strict_types=1);

use App\Models\Connection;
use App\Models\User;
use Illuminate\Encryption\Encrypter;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

/*
 * ATL-32 (security round 1, low severity): with APP_DEBUG on, an unhandled exception on /api/*
 * returns file paths and a stack trace to the browser. The shipped env now sets APP_DEBUG=false
 * (see EnvExampleTest); this proves the actual effect that was the finding, using a real failure
 * mode from the brief's own example: a Connection row whose secret can no longer be decrypted
 * with the current APP_KEY (an encrypted cast throws DecryptException, uncaught, on GET
 * /api/connections, which reads it to build api_key_last4).
 */
it('returns no trace or file in the JSON body of an unhandled exception when APP_DEBUG is off', function (): void {
    config(['app.debug' => false]);

    $owner = User::factory()->create();
    $this->actingAs($owner, 'web');

    // Ciphertext from a key the running app does not have: decrypting it throws.
    $foreignKey = Encrypter::generateKey((string) config('app.cipher'));
    $foreignCiphertext = (new Encrypter($foreignKey, (string) config('app.cipher')))->encryptString('wont-decrypt');

    DB::table('connections')->insert([
        'id' => (string) Str::uuid(),
        'exchange' => 'okx',
        'env' => 'demo',
        'label' => 'OKX Demo',
        'api_key' => $foreignCiphertext,
        'api_secret' => $foreignCiphertext,
        'passphrase' => $foreignCiphertext,
        'created_at' => now(),
        'updated_at' => now(),
    ]);
    expect(Connection::query()->count())->toBe(1);

    $response = $this->getJson('/api/connections');

    $response->assertStatus(500);
    $body = $response->json();
    expect($body)->toBeArray()
        ->and($body)->not->toHaveKey('trace')
        ->and($body)->not->toHaveKey('file')
        ->and($body)->not->toHaveKey('line');
    expect($response->getContent())->not->toContain(__DIR__)
        ->not->toContain('DecryptException');
});

it('does leak file and trace for the same failure when APP_DEBUG is on, proving the test exercises the real risk', function (): void {
    config(['app.debug' => true]);

    $owner = User::factory()->create();
    $this->actingAs($owner, 'web');

    $foreignKey = Encrypter::generateKey((string) config('app.cipher'));
    $foreignCiphertext = (new Encrypter($foreignKey, (string) config('app.cipher')))->encryptString('wont-decrypt');

    DB::table('connections')->insert([
        'id' => (string) Str::uuid(),
        'exchange' => 'okx',
        'env' => 'demo',
        'label' => 'OKX Demo',
        'api_key' => $foreignCiphertext,
        'api_secret' => $foreignCiphertext,
        'passphrase' => $foreignCiphertext,
        'created_at' => now(),
        'updated_at' => now(),
    ]);

    $body = $this->getJson('/api/connections')->assertStatus(500)->json();

    expect($body)->toHaveKeys(['trace', 'file']);
});
