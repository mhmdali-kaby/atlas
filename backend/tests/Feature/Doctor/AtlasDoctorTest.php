<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Connection;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\Request;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\Http;

const ATL13_CORE = 'http://127.0.0.1:8100';
const ATL13_SECRET = 'atl13-test-only-internal-secret';
const ATL13_KEY = 'atl13-api-key-wxyz';
const ATL13_API_SECRET = 'atl13-api-secret-value';
const ATL13_PASSPHRASE = 'atl13-passphrase-value';
const ATL13_FULL_UID = '4471029384756123';

const ATL13_CHECKS = [
    'postgres', 'postgres_extensions', 'migrations', 'redis', 'trading_core', 'okx_clock',
    'demo_connection', 'withdraw_permission', 'disk_space', 'security', 'backup',
];

/**
 * @param  array<string, mixed>  $test  overrides for trading_core's connection-test body
 * @param  array<string, mixed>  $time  overrides for /internal/time
 */
function fakeCore(array $test = [], array $time = []): void
{
    Http::fake(function (Request $request) use ($test, $time) {
        $path = (string) parse_url($request->url(), PHP_URL_PATH);
        if ($path === '/health') {
            return Http::response(['status' => 'ok', 'env' => 'demo', 'version' => '0.1.0']);
        }
        expect($request->hasHeader('X-Atlas-Signature'))->toBeTrue();
        if ($path === '/internal/time') {
            return Http::response([
                'ok' => true, 'env' => 'demo', 'offset_ms' => 120, 'drift_ms' => 120,
                'server_time' => '2026-10-28T08:00:00.120Z', 'local_time' => '2026-10-28T08:00:00.000Z', ...$time,
            ]);
        }
        if (preg_match('#^/internal/connections/[0-9a-f-]+/test$#', $path) === 1) {
            return Http::response([
                'ok' => true, 'acct_lv' => '2', 'account_mode' => 'futures', 'position_mode' => 'net_mode',
                'permissions' => ['read_only', 'trade'], 'uid_last4' => '6123', 'error' => null,
                'okx_code' => null, 'verified_at' => '2026-10-28T08:00:00.000000Z', ...$test,
            ]);
        }

        return Http::response(['detail' => 'not found'], 404);
    });
}

/**
 * @return array{0: int, 1: string}
 */
function doctor(bool $json = false): array
{
    $code = Artisan::call('atlas:doctor', $json ? ['--json' => true] : []);

    return [$code, Artisan::output()];
}

function lineFor(string $output, string $name): string
{
    foreach (explode("\n", $output) as $line) {
        if (preg_match('/^[✓✗⚠] '.preg_quote($name, '/').' /u', $line) === 1) {
            return $line;
        }
    }
    throw new RuntimeException("no line for {$name} in:\n{$output}");
}

function expectNoSecrets(string $output): void
{
    foreach ([ATL13_KEY, ATL13_API_SECRET, ATL13_PASSPHRASE, ATL13_FULL_UID, ATL13_SECRET] as $secret) {
        expect($output)->not->toContain($secret);
    }
}

beforeEach(function (): void {
    config([
        'services.trading_core.url' => ATL13_CORE,
        'services.trading_core.secret' => ATL13_SECRET,
        'atlas_defaults.doctor.min_free_disk_gb' => 0,
    ]);
    $this->connection = Connection::query()->create([
        'exchange' => 'okx', 'env' => 'demo', 'label' => 'OKX Demo',
        'api_key' => ATL13_KEY, 'api_secret' => ATL13_API_SECRET, 'passphrase' => ATL13_PASSPHRASE,
        'permissions' => ['read_only', 'trade'], 'uid_last4' => '6123', 'last_verified_at' => now(),
    ]);
});

it('passes with everything up and a verified demo connection', function (): void {
    fakeCore();

    [$code, $output] = doctor();

    expect($code)->toBe(0);
    foreach (array_diff(ATL13_CHECKS, ['backup']) as $name) {
        expect(lineFor($output, $name))->toStartWith('✓');
    }
    expect(lineFor($output, 'backup'))->toStartWith('⚠')->toContain('not configured');
    expect(lineFor($output, 'demo_connection'))->toContain('UID …6123');
    expect(lineFor($output, 'okx_clock'))->toContain('120 ms');
    expectNoSecrets($output);

    // The re-run test is signed and audited like the Settings button.
    Http::assertSent(fn (Request $r): bool => str_ends_with($r->url(), "/internal/connections/{$this->connection->id}/test")
        && $r->method() === 'POST' && $r->hasHeader('X-Atlas-Signature'));
    $audit = AuditLog::query()->where('action', 'connection.tested')->sole();
    expect($audit->after['source'] ?? null)->toBe('atlas:doctor');
    expectNoSecrets(json_encode($audit->after, JSON_THROW_ON_ERROR));
});

it('fails with "unreachable" when trading_core is stopped', function (): void {
    Http::fake(fn () => throw new ConnectionException('Connection refused'));

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'trading_core'))->toStartWith('✗')->toContain('unreachable');
    expect(lineFor($output, 'okx_clock'))->toStartWith('✗');
    expect(lineFor($output, 'demo_connection'))->toStartWith('✗');
    expect(lineFor($output, 'postgres'))->toStartWith('✓');
    expectNoSecrets($output);
});

/**
 * A live OKX connection whose last test stored the withdraw permission (trading_core refused it).
 */
function liveWithdrawConnection(): Connection
{
    return Connection::query()->create([
        'exchange' => 'okx', 'env' => 'live', 'label' => 'OKX Live',
        'api_key' => 'atl30-live-key', 'api_secret' => 'atl30-live-secret', 'passphrase' => 'atl30-live-pass',
        'permissions' => ['read_only', 'trade', 'withdraw'], 'last_test_error' => 'withdraw_permission_enabled',
    ]);
}

it('warns, and still exits 0, when the demo key can withdraw (ATL-30)', function (): void {
    fakeCore(['permissions' => ['read_only', 'trade', 'withdraw'], 'warnings' => ['withdraw_permission_enabled']]);

    [$code, $output] = doctor();

    expect($code)->toBe(0);
    expect(lineFor($output, 'withdraw_permission'))->toStartWith('⚠')->toContain('withdraw permission')->toContain('demo');
    expect(lineFor($output, 'demo_connection'))->toStartWith('✓');
    expectNoSecrets($output);
});

it('fails naming the withdraw permission when the live key can withdraw', function (): void {
    liveWithdrawConnection();
    fakeCore();

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'withdraw_permission'))->toStartWith('✗')->toContain('withdraw permission')->toContain('live');
    expect(lineFor($output, 'demo_connection'))->toStartWith('✓');
    expectNoSecrets($output);
});

it('fails on a withdraw-enabled live key even when the demo key can withdraw too', function (): void {
    liveWithdrawConnection();
    fakeCore(['permissions' => ['read_only', 'trade', 'withdraw'], 'warnings' => ['withdraw_permission_enabled']]);

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'withdraw_permission'))->toStartWith('✗')->toContain('live');
});

it('warns on the demo withdraw permission from the stored permissions when trading_core is down', function (): void {
    $this->connection->update(['permissions' => ['read_only', 'trade', 'withdraw']]);
    Http::fake(fn () => throw new ConnectionException('Connection refused'));

    [$code, $output] = doctor();

    expect($code)->not->toBe(0); // trading_core is down
    expect(lineFor($output, 'withdraw_permission'))->toStartWith('⚠')->toContain('withdraw permission');
});

it('fails when there is no demo connection', function (): void {
    $this->connection->delete();
    fakeCore();

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'demo_connection'))->toStartWith('✗')->toContain('No OKX demo connection');
    expect(lineFor($output, 'withdraw_permission'))->toStartWith('✗');
});

it('fails when the clock drift is 2 s or more', function (): void {
    fakeCore(time: ['offset_ms' => -2000, 'drift_ms' => 2000]);
    [$code, $output] = doctor();
    expect($code)->not->toBe(0);
    expect(lineFor($output, 'okx_clock'))->toStartWith('✗')->toContain('2000 ms');
});

it('fails when OKX public/time is unreachable from trading_core', function (): void {
    Http::fake(fn (Request $r) => str_ends_with($r->url(), '/health')
        ? Http::response(['status' => 'ok', 'env' => 'demo', 'version' => '0.1.0'])
        : Http::response(['ok' => false, 'error' => 'okx_unreachable'], 502));
    [, $output] = doctor();
    expect(lineFor($output, 'okx_clock'))->toStartWith('✗')->toContain('unreachable');
});

it('fails when free disk space is under the minimum', function (): void {
    config(['atlas_defaults.doctor.min_free_disk_gb' => 1_000_000]);
    fakeCore();

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'disk_space'))->toStartWith('✗')->toContain('1000000 GB');
});

it('fails when the session lifetime is over 15 minutes', function (): void {
    config(['session.lifetime' => 120]);
    fakeCore();

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'security'))->toStartWith('✗')->toContain('120 min');
});

it('fails when debug is on outside local', function (): void {
    config(['app.debug' => true, 'app.env' => 'production']);
    fakeCore();

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'security'))->toStartWith('✗')->toContain('production');
});

it('fails security when debug is on even though APP_ENV is local', function (): void {
    // ATL-35: the shipped APP_ENV is local, so a check gated on "outside local" never fired.
    config(['app.debug' => true, 'app.env' => 'local']);
    fakeCore();

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'security'))->toStartWith('✗')->toContain('local');
});

it('prints one JSON object per check with name, ok and detail', function (): void {
    liveWithdrawConnection();
    fakeCore();

    [$code, $output] = doctor(json: true);

    expect($code)->not->toBe(0);
    $checks = json_decode($output, true, flags: JSON_THROW_ON_ERROR);
    expect($checks)->toBeArray()->toHaveCount(count(ATL13_CHECKS));
    expect(array_column($checks, 'name'))->toBe(ATL13_CHECKS);
    foreach ($checks as $check) {
        expect($check)->toHaveKeys(['name', 'ok', 'status', 'detail']);
        expect($check['ok'])->toBeBool();
        expect($check['detail'])->toBeString()->not->toBeEmpty();
    }
    $byName = array_column($checks, null, 'name');
    expect($byName['withdraw_permission']['ok'])->toBeFalse();
    expect($byName['backup'])->toMatchArray(['ok' => true, 'status' => 'warn']);
    expect($byName['postgres']['ok'])->toBeTrue();
    expectNoSecrets($output);
});
