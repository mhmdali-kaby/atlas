<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Connection;
use App\Models\User;
use Illuminate\Contracts\Debug\ExceptionHandler;
use Illuminate\Foundation\Exceptions\Handler;
use Illuminate\Log\Events\MessageLogged;
use Illuminate\Support\Facades\Crypt;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Event;
use Illuminate\Validation\ValidationException;

const ATL4_KEY = 'a1b2c3d4-e5f6-7a8b-9c0d-KEYSECRETab12';
const ATL4_SECRET = 'S3CR3T0123456789ABCDEF0123456789';
const ATL4_PASSPHRASE = 'Pass-Phrase-ZZ99';

/**
 * @param  array<string, string>  $overrides
 * @return array<string, string>
 */
function demoConnectionPayload(array $overrides = []): array
{
    return [
        'env' => 'demo',
        'label' => 'OKX Demo',
        'api_key' => ATL4_KEY,
        'api_secret' => ATL4_SECRET,
        'passphrase' => ATL4_PASSPHRASE,
        ...$overrides,
    ];
}

function assertNoSecretsIn(string $haystack): void
{
    foreach ([ATL4_KEY, ATL4_SECRET, ATL4_PASSPHRASE] as $secret) {
        expect($haystack)->not->toContain($secret);
    }
}

beforeEach(function (): void {
    $this->owner = User::factory()->create();
    $this->actingAs($this->owner, 'web');
});

it('requires authentication', function (): void {
    auth()->guard('web')->logout();

    $this->getJson('/api/connections')->assertUnauthorized();
    $this->postJson('/api/connections', demoConnectionPayload())->assertUnauthorized();
    expect(Connection::query()->count())->toBe(0);
});

it('creates a demo connection and returns only the last 4 characters of the key', function (): void {
    $response = $this->postJson('/api/connections', demoConnectionPayload())
        ->assertCreated()
        ->assertJsonPath('data.exchange', 'okx')
        ->assertJsonPath('data.env', 'demo')
        ->assertJsonPath('data.label', 'OKX Demo')
        ->assertJsonPath('data.api_key_last4', 'ab12')
        ->assertJsonPath('data.has_secret', true)
        ->assertJsonPath('data.has_passphrase', true)
        ->assertJsonPath('data.last_verified_at', null)
        ->assertJsonMissingPath('data.api_key')
        ->assertJsonMissingPath('data.api_secret')
        ->assertJsonMissingPath('data.passphrase');

    assertNoSecretsIn((string) $response->getContent());

    $list = $this->getJson('/api/connections')
        ->assertOk()
        ->assertJsonCount(1, 'data')
        ->assertJsonPath('data.0.api_key_last4', 'ab12');
    assertNoSecretsIn((string) $list->getContent());
});

it('stores ciphertext in Postgres that decrypts to the submitted values', function (): void {
    $id = $this->postJson('/api/connections', demoConnectionPayload())->assertCreated()->json('data.id');

    $row = DB::selectOne('select api_key, api_secret, passphrase from connections where id = ?', [$id]);

    expect($row->api_key)->not->toBe(ATL4_KEY)
        ->and($row->api_secret)->not->toBe(ATL4_SECRET)
        ->and($row->passphrase)->not->toBe(ATL4_PASSPHRASE);
    assertNoSecretsIn($row->api_key.$row->api_secret.$row->passphrase);

    // Laravel `encrypted` cast format: encryptString (no PHP serialisation), so trading_core can read it.
    expect(Crypt::decryptString($row->api_key))->toBe(ATL4_KEY)
        ->and(Crypt::decryptString($row->api_secret))->toBe(ATL4_SECRET)
        ->and(Crypt::decryptString($row->passphrase))->toBe(ATL4_PASSPHRASE);
});

it('refuses a second connection for the same exchange and env', function (): void {
    $this->postJson('/api/connections', demoConnectionPayload())->assertCreated();

    $this->postJson('/api/connections', demoConnectionPayload(['label' => 'Another']))
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['env' => __('connections.already_exists')]);

    expect(Connection::query()->count())->toBe(1);
});

it('refuses live connections in Phase 0', function (): void {
    $this->postJson('/api/connections', demoConnectionPayload(['env' => 'live']))
        ->assertUnprocessable()
        ->assertJsonPath('message', 'Live connections are enabled in Phase 4')
        ->assertJsonValidationErrors(['env' => 'Live connections are enabled in Phase 4']);

    expect(Connection::query()->count())->toBe(0);
});

it('rejects an unknown env and missing fields', function (): void {
    $this->postJson('/api/connections', demoConnectionPayload(['env' => 'paper']))
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['env']);

    $this->postJson('/api/connections', ['env' => 'demo'])
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['label', 'api_key', 'api_secret', 'passphrase']);
});

it('keeps omitted secrets when PATCH changes only the label', function (): void {
    $id = $this->postJson('/api/connections', demoConnectionPayload())->json('data.id');

    $this->patchJson("/api/connections/{$id}", ['label' => 'Renamed'])
        ->assertOk()
        ->assertJsonPath('data.label', 'Renamed')
        ->assertJsonPath('data.api_key_last4', 'ab12');

    $row = DB::selectOne('select api_key, api_secret, passphrase from connections where id = ?', [$id]);
    expect(Crypt::decryptString($row->api_key))->toBe(ATL4_KEY)
        ->and(Crypt::decryptString($row->api_secret))->toBe(ATL4_SECRET)
        ->and(Crypt::decryptString($row->passphrase))->toBe(ATL4_PASSPHRASE);
});

it('replaces a secret on PATCH and clears the old verification', function (): void {
    $id = $this->postJson('/api/connections', demoConnectionPayload())->json('data.id');
    Connection::query()->whereKey($id)->update([
        'permissions' => json_encode(['read_only', 'trade']),
        'account_mode' => '2',
        'uid_last4' => '9876',
        'last_verified_at' => now(),
    ]);

    $this->patchJson("/api/connections/{$id}", ['api_key' => 'NEWKEY-zz99', 'api_secret' => 'new-secret-value'])
        ->assertOk()
        ->assertJsonPath('data.api_key_last4', 'zz99')
        ->assertJsonPath('data.permissions', null)
        ->assertJsonPath('data.uid_last4', null)
        ->assertJsonPath('data.last_verified_at', null);

    $connection = Connection::query()->findOrFail($id);
    expect($connection->api_key)->toBe('NEWKEY-zz99')
        ->and($connection->api_secret)->toBe('new-secret-value')
        ->and($connection->passphrase)->toBe(ATL4_PASSPHRASE);

    $audit = AuditLog::query()->where('action', 'connection.updated')->sole();
    expect($audit->after['secrets_changed'] ?? null)->toBe(['api_key', 'api_secret']);
    $auditJson = json_encode([$audit->before, $audit->after], JSON_THROW_ON_ERROR);
    assertNoSecretsIn($auditJson);
    expect($auditJson)->not->toContain('NEWKEY-zz99')->not->toContain('new-secret-value');
});

it('refuses to change env or exchange on PATCH', function (): void {
    $id = $this->postJson('/api/connections', demoConnectionPayload())->json('data.id');

    $this->patchJson("/api/connections/{$id}", ['env' => 'live', 'exchange' => 'binance'])
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['env', 'exchange']);
});

it('deletes a connection and returns 404 for unknown ids', function (): void {
    $id = $this->postJson('/api/connections', demoConnectionPayload())->json('data.id');

    $this->deleteJson("/api/connections/{$id}")->assertNoContent();
    expect(Connection::query()->count())->toBe(0);

    $this->deleteJson("/api/connections/{$id}")->assertNotFound();
    $this->patchJson('/api/connections/not-a-uuid', ['label' => 'x'])->assertNotFound();
});

it('audits create, update and delete without any secret value', function (): void {
    $id = $this->postJson('/api/connections', demoConnectionPayload())->json('data.id');
    $this->patchJson("/api/connections/{$id}", ['label' => 'Renamed'])->assertOk();
    $this->deleteJson("/api/connections/{$id}")->assertNoContent();

    $rows = AuditLog::query()->where('subject_type', 'connections')->where('subject_id', $id)
        ->orderBy('created_at')->get();

    expect($rows->pluck('action')->all())->toBe(['connection.created', 'connection.updated', 'connection.deleted']);
    expect($rows->pluck('user_id')->unique()->values()->all())->toBe([$this->owner->id]);

    [$created, $updated, $deleted] = $rows->all();
    expect($created->before)->toBeNull()
        ->and($created->after)->toMatchArray(['env' => 'demo', 'label' => 'OKX Demo', 'api_key_last4' => 'ab12'])
        ->and($updated->before['label'] ?? null)->toBe('OKX Demo')
        ->and($updated->after['label'] ?? null)->toBe('Renamed')
        ->and($updated->after['secrets_changed'] ?? null)->toBe([])
        ->and($deleted->before['label'] ?? null)->toBe('Renamed')
        ->and($deleted->after)->toBeNull();

    // Raw jsonb text, not the decoded arrays, so nothing slips through a cast.
    $raw = DB::select("select before::text as b, after::text as a from audit_log where subject_type = 'connections'");
    assertNoSecretsIn(json_encode($raw, JSON_THROW_ON_ERROR));
});

it('never writes secrets to the log or exception context on a validation failure', function (): void {
    $logFile = storage_path('logs/atl4-secret-test-'.bin2hex(random_bytes(4)).'.log');
    config([
        'logging.default' => 'atl4',
        'logging.channels.atl4' => ['driver' => 'single', 'path' => $logFile, 'level' => 'debug'],
    ]);

    /** @var list<array{message: string, context: array<string, mixed>}> $logged */
    $logged = [];
    Event::listen(MessageLogged::class, function (MessageLogged $e) use (&$logged): void {
        $logged[] = ['message' => $e->message, 'context' => $e->context];
    });

    // Validation failures are normally not reported; force it so the full report path runs.
    $handler = app(ExceptionHandler::class);
    assert($handler instanceof Handler);
    $handler->stopIgnoring(ValidationException::class);

    try {
        $response = $this->postJson('/api/connections', demoConnectionPayload(['label' => '']))
            ->assertUnprocessable()
            ->assertJsonValidationErrors(['label']);
        assertNoSecretsIn((string) $response->getContent());

        expect($logged)->not->toBeEmpty();
        $logText = (string) file_get_contents($logFile);
        expect($logText)->toContain('ValidationException');
        assertNoSecretsIn($logText);

        $sawException = false;
        foreach ($logged as $entry) {
            assertNoSecretsIn($entry['message']);
            $context = $entry['context'];
            unset($context['exception']);
            assertNoSecretsIn((string) json_encode($context, JSON_PARTIAL_OUTPUT_ON_ERROR));
            $exception = $entry['context']['exception'] ?? null;
            if ($exception instanceof ValidationException) {
                $sawException = true;
                assertNoSecretsIn($exception->getMessage());
                assertNoSecretsIn(json_encode($exception->errors(), JSON_THROW_ON_ERROR));
                assertNoSecretsIn($exception->getTraceAsString());
            }
        }
        expect($sawException)->toBeTrue();

        // The exception handler never flashes these fields back into the session.
        $dontFlash = (fn () => $this->dontFlash)->call($handler);
        expect($dontFlash)->toContain('api_key', 'api_secret', 'passphrase');
    } finally {
        @unlink($logFile);
    }
});
