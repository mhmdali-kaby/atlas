<?php

declare(strict_types=1);

namespace App\Support\Doctor;

use App\Models\Connection;
use App\Support\Auditable;
use App\Support\TradingCore;
use Illuminate\Database\Migrations\Migrator;
use Illuminate\Http\Client\Factory as HttpFactory;
use Illuminate\Process\Factory as ProcessFactory;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Redis;
use Throwable;

/**
 * The app-level health checks behind `php artisan atlas:doctor` (brief §18, Phase 0 gate). Docker
 * and port checks live in DevOps' `./atlas` wrapper. The OKX key check reuses trading_core's signed
 * connection test (ATL-8) so the withdraw rule exists in one place.
 */
final class Doctor
{
    /** Required extensions on the Atlas database (decision #233). */
    private const EXTENSIONS = ['timescaledb', 'vector'];

    private const WITHDRAW = 'withdraw';

    private const MIN_REVERB_SECRET_LENGTH = 32;

    /**
     * Decision #242: the Agent SDK must never see these, and neither should this command's own
     * probe of the CLI — an API key in the environment must not make "logged in" look true.
     */
    private const FORBIDDEN_ENV = ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'];

    private bool $databaseUp = false;

    private bool $tradingCoreUp = false;

    /** @var array<string, mixed>|null trading_core's connection-test body, when it ran */
    private ?array $testResult = null;

    private ?Connection $connection = null;

    private ?string $testFailure = null;

    /** The `claude` binary the install check found, once it has passed. */
    private ?string $claudeBin = null;

    public function __construct(
        private readonly HttpFactory $http,
        private readonly Migrator $migrator,
        private readonly Auditable $audit,
        private readonly ProcessFactory $process,
    ) {}

    /**
     * @return list<CheckResult>
     */
    public function run(): array
    {
        return [
            $this->postgres(),
            $this->extensions(),
            $this->migrations(),
            $this->redis(),
            $this->tradingCore(),
            $this->okxClock(),
            $this->demoConnection(),
            $this->withdrawPermission(),
            $this->diskSpace(),
            $this->security(),
            $this->reverbSecret(),
            $this->claudeCli(),
            $this->claudeLogin(),
            $this->claudeApiKey(),
            $this->backup(),
        ];
    }

    /**
     * ATL-113: only the `claude` CLI checks (installed, logged in, no Anthropic key variable),
     * for GET /api/agents/claude-status. Same order and rules as in run().
     *
     * @return list<CheckResult>
     */
    public function claudeChecks(): array
    {
        $this->claudeBin = null;

        return [$this->claudeCli(), $this->claudeLogin(), $this->claudeApiKey()];
    }

    private function postgres(): CheckResult
    {
        try {
            $row = DB::selectOne('select version() as v');
            $this->databaseUp = true;
            $version = is_object($row) && isset($row->v) && is_string($row->v) ? $row->v : '';
            preg_match('/PostgreSQL [0-9.]+/', $version, $m);

            return CheckResult::pass('postgres', self::t('postgres_ok', ['version' => $m[0] ?? 'PostgreSQL']));
        } catch (Throwable $e) {
            return CheckResult::fail('postgres', self::t('postgres_down', ['reason' => self::reason($e)]));
        }
    }

    private function extensions(): CheckResult
    {
        if (! $this->databaseUp) {
            return CheckResult::fail('postgres_extensions', self::t('skipped_no_db'));
        }
        try {
            /** @var list<string> $present */
            $present = DB::table('pg_extension')->whereIn('extname', self::EXTENSIONS)->pluck('extname')->all();
        } catch (Throwable $e) {
            return CheckResult::fail('postgres_extensions', self::t('query_failed', ['reason' => self::reason($e)]));
        }
        $missing = array_values(array_diff(self::EXTENSIONS, $present));

        return $missing === []
            ? CheckResult::pass('postgres_extensions', self::t('extensions_ok', ['list' => implode(', ', self::EXTENSIONS)]))
            : CheckResult::fail('postgres_extensions', self::t('extensions_missing', ['list' => implode(', ', $missing)]));
    }

    private function migrations(): CheckResult
    {
        if (! $this->databaseUp) {
            return CheckResult::fail('migrations', self::t('skipped_no_db'));
        }
        try {
            if (! $this->migrator->repositoryExists()) {
                return CheckResult::fail('migrations', self::t('migrations_none'));
            }
            $files = $this->migrator->getMigrationFiles([database_path('migrations'), ...$this->migrator->paths()]);
            $ran = $this->migrator->getRepository()->getRan();
        } catch (Throwable $e) {
            return CheckResult::fail('migrations', self::t('query_failed', ['reason' => self::reason($e)]));
        }
        $pending = array_values(array_diff(array_keys($files), $ran));

        return $pending === []
            ? CheckResult::pass('migrations', self::t('migrations_ok', ['count' => count($ran)]))
            : CheckResult::fail('migrations', self::t('migrations_pending', ['count' => count($pending)]));
    }

    private function redis(): CheckResult
    {
        try {
            Redis::connection()->command('ping');

            return CheckResult::pass('redis', self::t('redis_ok'));
        } catch (Throwable $e) {
            return CheckResult::fail('redis', self::t('redis_down', ['reason' => self::reason($e)]));
        }
    }

    private function tradingCore(): CheckResult
    {
        $url = config('services.trading_core.url');
        $url = is_string($url) ? rtrim($url, '/') : 'http://127.0.0.1:8100';
        try {
            $response = $this->http->baseUrl($url)->timeout(self::int('trading_core_timeout_seconds', 5))
                ->acceptJson()->get('/health');
        } catch (Throwable) {
            return CheckResult::fail('trading_core', self::t('trading_core_unreachable', ['url' => $url]));
        }
        $body = $response->json();
        if (! $response->successful() || ! is_array($body) || ($body['status'] ?? null) !== 'ok') {
            return CheckResult::fail('trading_core', self::t('trading_core_unhealthy', ['status' => $response->status()]));
        }
        $this->tradingCoreUp = true;

        return CheckResult::pass('trading_core', self::t('trading_core_ok', [
            'env' => self::str($body['env'] ?? null), 'version' => self::str($body['version'] ?? null),
        ]));
    }

    private function okxClock(): CheckResult
    {
        if (! $this->tradingCoreUp) {
            return CheckResult::fail('okx_clock', self::t('skipped_no_core'));
        }
        try {
            $response = TradingCore::fromConfig()->get('/internal/time');
        } catch (Throwable) {
            return CheckResult::fail('okx_clock', self::t('core_call_failed'));
        }
        $body = $response->json();
        if (! is_array($body)) {
            return CheckResult::fail('okx_clock', self::t('core_error', ['status' => $response->status()]));
        }
        if (! $response->successful() || ($body['ok'] ?? false) !== true) {
            return ($body['error'] ?? null) === 'okx_unreachable'
                ? CheckResult::fail('okx_clock', self::t('okx_unreachable'))
                : CheckResult::fail('okx_clock', self::t('core_error', ['status' => $response->status()]));
        }
        $drift = is_int($body['drift_ms'] ?? null) ? $body['drift_ms'] : null;
        $max = self::int('max_clock_drift_ms', 2000);
        if ($drift === null) {
            return CheckResult::fail('okx_clock', self::t('core_error', ['status' => $response->status()]));
        }

        return $drift < $max
            ? CheckResult::pass('okx_clock', self::t('clock_ok', ['drift' => $drift, 'max' => $max]))
            : CheckResult::fail('okx_clock', self::t('clock_drift', ['drift' => $drift, 'max' => $max]));
    }

    private function demoConnection(): CheckResult
    {
        if (! $this->databaseUp) {
            return CheckResult::fail('demo_connection', self::t('skipped_no_db'));
        }
        try {
            $this->connection = Connection::query()->where('exchange', 'okx')->where('env', 'demo')->first();
        } catch (Throwable $e) {
            $this->testFailure = self::t('query_failed', ['reason' => self::reason($e)]);

            return CheckResult::fail('demo_connection', $this->testFailure);
        }
        if ($this->connection === null) {
            return CheckResult::fail('demo_connection', self::t('connection_missing'));
        }
        $previously = $this->connection->last_verified_at !== null
            ? self::t('previously_passed')
            : self::t('previously_not_passed');
        $connection = $this->connection;
        if (! $this->tradingCoreUp) {
            $this->testFailure = self::t('skipped_no_core');

            return CheckResult::fail('demo_connection', "{$this->testFailure} ({$previously})");
        }

        try {
            $response = TradingCore::fromConfig()->post("/internal/connections/{$connection->id}/test");
        } catch (Throwable) {
            $this->testFailure = self::t('core_call_failed');

            return CheckResult::fail('demo_connection', $this->testFailure);
        }
        $body = $response->json();
        if (! $response->successful() || ! is_array($body) || ! array_key_exists('ok', $body)) {
            $this->testFailure = self::t('core_error', ['status' => $response->status()]);

            return CheckResult::fail('demo_connection', $this->testFailure);
        }
        /** @var array<string, mixed> $body */
        $this->testResult = $body;
        $this->auditTest($connection, $body);

        $summary = self::t('connection_summary', [
            'label' => $connection->label,
            'mode' => self::str($body['account_mode'] ?? null),
            'position' => self::str($body['position_mode'] ?? null),
            'uid' => self::str($body['uid_last4'] ?? null),
        ]);
        if ($body['ok'] === true) {
            return CheckResult::pass('demo_connection', self::t('connection_ok', ['summary' => $summary, 'previously' => $previously]));
        }

        return CheckResult::fail('demo_connection', self::t('connection_failed', [
            'message' => self::errorMessage($body['error'] ?? null), 'previously' => $previously,
        ]));
    }

    /**
     * Withdraw permission (decision #237 as relaxed by ATL-30): a live key with withdraw fails; a demo
     * key with withdraw is accepted by the CEO's instruction and only warns. Live is read from the
     * stored result of its last connection test (doctor only re-tests the demo key in Phase 0).
     */
    private function withdrawPermission(): CheckResult
    {
        try {
            $live = $this->databaseUp
                ? Connection::query()->where('exchange', 'okx')->where('env', 'live')->first()
                : null;
        } catch (Throwable $e) {
            return CheckResult::fail('withdraw_permission', self::t('query_failed', ['reason' => self::reason($e)]));
        }
        if ($live !== null && in_array(self::WITHDRAW, self::stringList($live->permissions), true)) {
            return CheckResult::fail('withdraw_permission', self::t('withdraw_enabled_live'));
        }

        if ($this->connection === null) {
            return CheckResult::fail('withdraw_permission', $this->testFailure ?? self::t('withdraw_no_connection'));
        }
        if ($this->testResult !== null && ($this->testResult['account_mode'] ?? null) !== null) {
            $permissions = $this->testResult['permissions'] ?? [];
            $source = self::t('withdraw_source_now');
        } else {
            $permissions = $this->connection->permissions ?? [];
            $source = self::t('withdraw_source_stored');
        }
        $permissions = self::stringList($permissions);

        if (in_array(self::WITHDRAW, $permissions, true)) {
            return CheckResult::warn('withdraw_permission', self::t('withdraw_enabled_demo', ['source' => $source]));
        }
        if ($this->testResult === null || ($this->testResult['account_mode'] ?? null) === null) {
            // We never saw what OKX says today; a stored "no withdraw" is not proof.
            return CheckResult::fail('withdraw_permission', self::t('withdraw_unknown', ['reason' => $this->testFailure ?? self::errorMessage($this->testResult['error'] ?? null)]));
        }

        return CheckResult::pass('withdraw_permission', self::t('withdraw_off', ['permissions' => implode(', ', $permissions)]));
    }

    private function diskSpace(): CheckResult
    {
        $free = @disk_free_space(base_path());
        if ($free === false) {
            return CheckResult::fail('disk_space', self::t('disk_unknown'));
        }
        $min = self::int('min_free_disk_gb', 5);
        $gb = number_format($free / 1024 ** 3, 1, '.', '');

        return $free >= $min * 1024 ** 3
            ? CheckResult::pass('disk_space', self::t('disk_ok', ['free' => $gb, 'min' => $min]))
            : CheckResult::fail('disk_space', self::t('disk_low', ['free' => $gb, 'min' => $min]));
    }

    /**
     * ATL-33 AC4: an insecure session lifetime or debug mode left on outside local must fail
     * doctor, not just be caught in code review.
     */
    private function security(): CheckResult
    {
        $lifetime = (int) config('session.lifetime');
        $max = (int) config('atlas_defaults.doctor.max_session_lifetime_minutes');
        if ($lifetime > $max) {
            return CheckResult::fail('security', self::t('session_lifetime_too_long', ['lifetime' => $lifetime, 'max' => $max]));
        }

        $env = (string) config('app.env');
        // ATL-35: the shipped APP_ENV is local, so a check that only fired "outside local" never
        // ran in practice. APP_DEBUG leaks stack traces and config values to any requester
        // whatever the environment, so it fails here regardless of app.env.
        if ((bool) config('app.debug')) {
            return CheckResult::fail('security', self::t('debug_enabled', ['env' => $env]));
        }

        return CheckResult::pass('security', self::t('security_ok', ['lifetime' => $lifetime]));
    }

    private function backup(): CheckResult
    {
        // The Backup feature (brief §14.12) does not exist yet: a warning, never a failure.
        return CheckResult::warn('backup', self::t('backup_not_configured'));
    }

    /**
     * ATL-50 (security round 4, finding 1): an empty or short REVERB_APP_SECRET lets anyone compute
     * valid private-channel auth with HMAC(key=""), reading the War Room and account streams and
     * injecting fake messages. `./atlas up` generates one on first run; this only reports the truth.
     */
    private function reverbSecret(): CheckResult
    {
        $secret = (string) config('reverb.apps.apps.0.secret');
        if ($secret === '') {
            return CheckResult::fail('reverb_secret', self::t('reverb_secret_missing'));
        }
        $length = mb_strlen($secret);
        if ($length < self::MIN_REVERB_SECRET_LENGTH) {
            return CheckResult::fail('reverb_secret', self::t('reverb_secret_short', [
                'length' => $length, 'min' => self::MIN_REVERB_SECRET_LENGTH,
            ]));
        }

        return CheckResult::pass('reverb_secret', self::t('reverb_secret_ok', ['length' => $length]));
    }

    /**
     * Decision #242: agents run through the owner's logged-in `claude` CLI, never an API key.
     * Looks for `claude` on PATH, or at ATLAS_CLAUDE_BIN when set, and runs `claude --version`.
     */
    private function claudeCli(): CheckResult
    {
        $bin = self::claudeBinConfig();
        try {
            $result = $this->process->newPendingProcess()->timeout(self::int('claude_cli_timeout_seconds', 15))
                ->env(self::withoutAnthropicEnv())
                ->run([$bin, '--version']);
        } catch (Throwable $e) {
            return CheckResult::fail('claude_cli', self::t('claude_cli_missing', ['bin' => $bin, 'reason' => self::reason($e)]));
        }
        if (! $result->successful()) {
            return CheckResult::fail('claude_cli', self::t('claude_cli_missing', ['bin' => $bin, 'reason' => self::t('claude_cli_exit', ['code' => $result->exitCode() ?? -1])]));
        }
        $this->claudeBin = $bin;
        $version = trim(strtok(trim($result->output()), "\n") ?: '');

        return CheckResult::pass('claude_cli', self::t('claude_cli_ok', ['version' => self::str($version)]));
    }

    /**
     * A cheap non-interactive auth check: the CLI's own `auth status --json` when it understands
     * it, otherwise one short prompt turn. Never runs when claudeCli() did not find the CLI.
     */
    private function claudeLogin(): CheckResult
    {
        if ($this->claudeBin === null) {
            return CheckResult::fail('claude_cli_login', self::t('skipped_no_cli'));
        }
        $bin = $this->claudeBin;
        $timeout = self::int('claude_cli_timeout_seconds', 15);

        try {
            $status = $this->process->newPendingProcess()->timeout($timeout)->env(self::withoutAnthropicEnv())
                ->run([$bin, 'auth', 'status', '--json']);
            /** @var mixed $decoded */
            $decoded = json_decode(trim($status->output()), true);
        } catch (Throwable) {
            $decoded = null;
        }

        if (is_array($decoded) && array_key_exists('loggedIn', $decoded)) {
            return $decoded['loggedIn'] === true
                ? CheckResult::pass('claude_cli_login', self::t('claude_cli_login_ok'))
                : CheckResult::fail('claude_cli_login', self::t('claude_cli_login_failed'));
        }

        // This build of the CLI has no status command we could parse: fall back to one cheap,
        // non-interactive prompt turn, still with no API key reaching the process.
        try {
            $probe = $this->process->newPendingProcess()->timeout(self::int('claude_cli_probe_timeout_seconds', 30))
                ->env(self::withoutAnthropicEnv())
                ->run([$bin, '-p', 'ok', '--max-turns', '1']);
        } catch (Throwable) {
            return CheckResult::fail('claude_cli_login', self::t('claude_cli_login_failed'));
        }

        return $probe->successful()
            ? CheckResult::pass('claude_cli_login', self::t('claude_cli_login_ok'))
            : CheckResult::fail('claude_cli_login', self::t('claude_cli_login_failed'));
    }

    /**
     * Decision #242: an ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN in the environment the agents
     * process would inherit (backend/.env is loaded into this same process's env by Laravel's
     * own bootstrap, so checking here covers both) would let usage silently fall to
     * pay-as-you-go instead of the owner's subscription. Only the variable names are reported.
     */
    private function claudeApiKey(): CheckResult
    {
        $present = array_values(array_filter(self::FORBIDDEN_ENV, static fn (string $key): bool => self::envIsSet($key)));

        return $present === []
            ? CheckResult::pass('claude_api_key', self::t('claude_api_key_ok'))
            : CheckResult::fail('claude_api_key', self::t('claude_api_key_set', ['vars' => implode(', ', $present)]));
    }

    /**
     * @param  array<string, mixed>  $body
     */
    private function auditTest(Connection $connection, array $body): void
    {
        $after = $connection->auditSnapshot();
        foreach (['ok', 'acct_lv', 'account_mode', 'position_mode', 'permissions', 'uid_last4', 'error', 'okx_code'] as $field) {
            $after[$field] = $body[$field] ?? null;
        }
        $after['source'] = 'atlas:doctor';
        $this->audit->record('connection.tested', 'connections', $connection->id, null, $after);
    }

    private static function errorMessage(mixed $error): string
    {
        if (! is_string($error) || $error === '') {
            return self::t('unknown_error');
        }
        $key = "connections_test.{$error}";
        $message = __($key);

        return is_string($message) && $message !== $key ? $message : self::str(__('connections_test.okx_error'));
    }

    private static function reason(Throwable $e): string
    {
        // Exception class only: driver messages can echo DSNs and credentials.
        return class_basename($e);
    }

    /**
     * @param  array<string, scalar|null>  $replace
     */
    private static function t(string $key, array $replace = []): string
    {
        return self::str(__("doctor.{$key}", $replace));
    }

    /**
     * @return list<string>
     */
    private static function stringList(mixed $value): array
    {
        return is_array($value) ? array_values(array_filter($value, 'is_string')) : [];
    }

    private static function str(mixed $value): string
    {
        return is_string($value) && $value !== '' ? $value : '—';
    }

    private static function int(string $key, int $fallback): int
    {
        $value = config("atlas_defaults.doctor.{$key}");

        return is_int($value) ? $value : $fallback;
    }

    /** ATLAS_CLAUDE_BIN, or the bare command name so it is resolved from PATH. */
    private static function claudeBinConfig(): string
    {
        $bin = config('services.claude_code.bin');

        return is_string($bin) && $bin !== '' ? $bin : 'claude';
    }

    /**
     * Extra env for the Process call: both forbidden keys unset (Symfony Process removes an
     * inherited variable when its value is `false`), so this probe can never read "logged in"
     * off an API key that happens to be set.
     *
     * @return array<string, false>
     */
    private static function withoutAnthropicEnv(): array
    {
        return array_fill_keys(self::FORBIDDEN_ENV, false);
    }

    private static function envIsSet(string $key): bool
    {
        $value = $_ENV[$key] ?? $_SERVER[$key] ?? getenv($key);

        return is_string($value) && $value !== '';
    }
}
