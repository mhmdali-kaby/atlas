<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Connection;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\Request;
use Illuminate\Process\PendingProcess;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Process;

const ATL13_CORE = 'http://127.0.0.1:8100';
const ATL13_SECRET = 'atl13-test-only-internal-secret';
const ATL13_KEY = 'atl13-api-key-wxyz';
const ATL13_API_SECRET = 'atl13-api-secret-value';
const ATL13_PASSPHRASE = 'atl13-passphrase-value';
const ATL13_FULL_UID = '4471029384756123';

/** ATL-95, decision #242: never a real value, but it must never reach doctor's output either. */
const ATL95_API_KEY = 'atl95-test-only-anthropic-api-key';

const ATL13_CHECKS = [
    'postgres', 'postgres_extensions', 'migrations', 'redis', 'trading_core', 'okx_clock',
    'demo_connection', 'withdraw_permission', 'disk_space', 'security', 'reverb_secret',
    'claude_cli', 'claude_cli_login', 'claude_api_key', 'backup',
];

/** A valid 64-char Reverb secret, test-only (never the owner's real one). */
const ATL50_REVERB_SECRET = 'atl50-test-only-reverb-secret-0123456789abcdef0123456789abcdef';

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
    foreach ([ATL13_KEY, ATL13_API_SECRET, ATL13_PASSPHRASE, ATL13_FULL_UID, ATL13_SECRET, ATL95_API_KEY] as $secret) {
        expect($output)->not->toContain($secret);
    }
}

/**
 * ATL-95: fake the three `claude` CLI calls doctor may make. Defaults to installed and logged
 * in via `auth status --json`; pass `version`/`authStatus`/`probe` exit codes or output to make
 * one of them fail. `authStatus: null` makes the status command look unsupported (no usable
 * JSON), so doctor falls back to the `-p "ok" --max-turns 1` probe.
 *
 * @param  array{exitCode?: int, output?: string}  $version
 * @param  array{exitCode?: int, output?: string}|null  $authStatus
 * @param  array{exitCode?: int, output?: string}  $probe
 */
function fakeClaudeCli(array $version = [], ?array $authStatus = [], array $probe = []): void
{
    Process::fake(function (PendingProcess $process) use ($version, $authStatus, $probe) {
        $command = $process->command;
        if (! is_array($command)) {
            return Process::result(exitCode: 1);
        }
        if (in_array('--version', $command, true)) {
            return Process::result(output: $version['output'] ?? "2.4.1 (Claude Code)\n", exitCode: $version['exitCode'] ?? 0);
        }
        if (in_array('status', $command, true)) {
            if ($authStatus === null) {
                return Process::result(output: "unknown command 'status'\n", exitCode: 1);
            }

            return Process::result(
                output: $authStatus['output'] ?? json_encode(['loggedIn' => true]),
                exitCode: $authStatus['exitCode'] ?? 0,
            );
        }
        if (in_array('-p', $command, true)) {
            return Process::result(output: $probe['output'] ?? 'ok', exitCode: $probe['exitCode'] ?? 0);
        }

        return Process::result(exitCode: 1);
    });
}

beforeEach(function (): void {
    config([
        'services.trading_core.url' => ATL13_CORE,
        'services.trading_core.secret' => ATL13_SECRET,
        'atlas_defaults.doctor.min_free_disk_gb' => 0,
        'reverb.apps.apps.0.secret' => ATL50_REVERB_SECRET,
    ]);
    fakeClaudeCli();
    putenv('ANTHROPIC_API_KEY');
    putenv('ANTHROPIC_AUTH_TOKEN');
    unset($_ENV['ANTHROPIC_API_KEY'], $_ENV['ANTHROPIC_AUTH_TOKEN'], $_SERVER['ANTHROPIC_API_KEY'], $_SERVER['ANTHROPIC_AUTH_TOKEN']);
    $this->connection = Connection::query()->create([
        'exchange' => 'okx', 'env' => 'demo', 'label' => 'OKX Demo',
        'api_key' => ATL13_KEY, 'api_secret' => ATL13_API_SECRET, 'passphrase' => ATL13_PASSPHRASE,
        'permissions' => ['read_only', 'trade'], 'uid_last4' => '6123', 'last_verified_at' => now(),
    ]);
});

afterEach(function (): void {
    putenv('ANTHROPIC_API_KEY');
    putenv('ANTHROPIC_AUTH_TOKEN');
    unset($_ENV['ANTHROPIC_API_KEY'], $_ENV['ANTHROPIC_AUTH_TOKEN'], $_SERVER['ANTHROPIC_API_KEY'], $_SERVER['ANTHROPIC_AUTH_TOKEN']);
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

it('fails on an empty REVERB_APP_SECRET', function (): void {
    config(['reverb.apps.apps.0.secret' => '']);
    fakeCore();

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'reverb_secret'))->toStartWith('✗')->toContain('REVERB_APP_SECRET is empty');
});

it('fails on a REVERB_APP_SECRET under 32 characters', function (): void {
    config(['reverb.apps.apps.0.secret' => str_repeat('a', 31)]);
    fakeCore();

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'reverb_secret'))->toStartWith('✗')->toContain('31 chars');
});

it('passes with a REVERB_APP_SECRET of exactly 32 characters', function (): void {
    config(['reverb.apps.apps.0.secret' => str_repeat('a', 32)]);
    fakeCore();

    [$code, $output] = doctor();

    expect($code)->toBe(0);
    expect(lineFor($output, 'reverb_secret'))->toStartWith('✓');
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

// ATL-95, decision #242: agents run on the owner's logged-in claude CLI, never an API key.

it('fails the login check with a "claude login" hint when the CLI is not authenticated', function (): void {
    fakeCore();
    fakeClaudeCli(authStatus: ['output' => json_encode(['loggedIn' => false])]);

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'claude_cli'))->toStartWith('✓');
    expect(lineFor($output, 'claude_cli_login'))->toStartWith('✗')->toContain('claude login');
});

it('falls back to a prompt probe when the CLI has no usable auth status command', function (): void {
    fakeCore();
    fakeClaudeCli(authStatus: null, probe: ['exitCode' => 0]);

    [$code, $output] = doctor();

    expect($code)->toBe(0);
    expect(lineFor($output, 'claude_cli_login'))->toStartWith('✓');
    Process::assertRan(fn (PendingProcess $p): bool => is_array($p->command) && in_array('-p', $p->command, true));
});

it('fails the login check when the fallback prompt probe also fails', function (): void {
    fakeCore();
    fakeClaudeCli(authStatus: null, probe: ['exitCode' => 1]);

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'claude_cli_login'))->toStartWith('✗')->toContain('claude login');
});

it('fails the install check and never runs the auth check when claude is missing', function (): void {
    fakeCore();
    fakeClaudeCli(version: ['exitCode' => 127, 'output' => '']);

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'claude_cli'))->toStartWith('✗');
    expect(lineFor($output, 'claude_cli_login'))->toStartWith('✗')->toContain('not found');
    Process::assertNotRan(fn (PendingProcess $p): bool => is_array($p->command)
        && (in_array('status', $p->command, true) || in_array('-p', $p->command, true)));
});

it('fails the API key check when ANTHROPIC_API_KEY is set, without leaking its value', function (): void {
    fakeCore();
    putenv('ANTHROPIC_API_KEY='.ATL95_API_KEY);

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'claude_api_key'))->toStartWith('✗')->toContain('ANTHROPIC_API_KEY');
    expectNoSecrets($output);
});

it('fails the API key check when ANTHROPIC_AUTH_TOKEN is set', function (): void {
    fakeCore();
    putenv('ANTHROPIC_AUTH_TOKEN='.ATL95_API_KEY);

    [$code, $output] = doctor();

    expect($code)->not->toBe(0);
    expect(lineFor($output, 'claude_api_key'))->toStartWith('✗')->toContain('ANTHROPIC_AUTH_TOKEN');
    expectNoSecrets($output);
});

it('passes the API key check when neither Anthropic variable is set', function (): void {
    fakeCore();

    [$code, $output] = doctor();

    expect($code)->toBe(0);
    expect(lineFor($output, 'claude_api_key'))->toStartWith('✓');
});

it('never lets ANTHROPIC_API_KEY reach the claude subprocess calls', function (): void {
    fakeCore();
    putenv('ANTHROPIC_API_KEY='.ATL95_API_KEY);

    doctor();

    Process::assertRan(function (PendingProcess $p): bool {
        if (! is_array($p->command) || ! in_array('--version', $p->command, true)) {
            return false;
        }

        return ($p->environment['ANTHROPIC_API_KEY'] ?? null) === false;
    });
});
