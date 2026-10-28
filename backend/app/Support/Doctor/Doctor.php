<?php

declare(strict_types=1);

namespace App\Support\Doctor;

use App\Models\Connection;
use App\Support\Auditable;
use App\Support\TradingCore;
use Illuminate\Database\Migrations\Migrator;
use Illuminate\Http\Client\Factory as HttpFactory;
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

    private bool $databaseUp = false;

    private bool $tradingCoreUp = false;

    /** @var array<string, mixed>|null trading_core's connection-test body, when it ran */
    private ?array $testResult = null;

    private ?Connection $connection = null;

    private ?string $testFailure = null;

    public function __construct(
        private readonly HttpFactory $http,
        private readonly Migrator $migrator,
        private readonly Auditable $audit,
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
            $this->backup(),
        ];
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

    private function backup(): CheckResult
    {
        // The Backup feature (brief §14.12) does not exist yet: a warning, never a failure.
        return CheckResult::warn('backup', self::t('backup_not_configured'));
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
}
