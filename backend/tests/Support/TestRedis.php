<?php

declare(strict_types=1);

namespace Tests\Support;

/**
 * The per-checkout Redis key prefix for the event bus streams (ATL-211, decision #252).
 *
 * Every checkout prefixes every `atlas.*` stream key with `test_<the same 8 hex as its Postgres
 * test database>:`, so parallel checkouts sharing one Redis never read, drain or delete each
 * other's streams. `REDIS_TEST_PREFIX`, when set and non-empty, overrides the derived prefix.
 *
 * The same rule lives in Python in `atlas_shared.testing.redis_prefix`; both sides must derive
 * the same prefix from the same repository root so a Pest test and a pytest test in the same
 * checkout agree on which physical key `atlas.warroom` is.
 */
final class TestRedis
{
    public const OVERRIDE_VAR = 'REDIS_TEST_PREFIX';

    /** Read by App\Support\Events\EventBus::keyPrefix() via config('database.redis.events.options.prefix'). */
    public const ENV_VAR = 'ATLAS_REDIS_PREFIX';

    public static function derivedPrefix(?string $repoRoot = null): string
    {
        return 'test_'.substr(sha1($repoRoot ?? TestDatabase::repoRoot()), 0, 8).':';
    }

    public static function prefix(): string
    {
        $override = self::env(self::OVERRIDE_VAR);

        return $override !== null ? $override : self::derivedPrefix();
    }

    /**
     * Points ATLAS_REDIS_PREFIX at this checkout's prefix for the running process, so every
     * config() load (and any child process that inherits the environment, e.g. the `atlas:bridge`
     * process BridgeCommandTest spawns) sees it. Runs from the PHPUnit bootstrap, before any
     * Laravel application boots.
     */
    public static function configure(): string
    {
        $prefix = self::prefix();
        putenv(self::ENV_VAR.'='.$prefix);
        $_ENV[self::ENV_VAR] = $prefix;
        $_SERVER[self::ENV_VAR] = $prefix;

        return $prefix;
    }

    private static function env(string $key): ?string
    {
        $value = $_SERVER[$key] ?? $_ENV[$key] ?? getenv($key);

        return is_string($value) && $value !== '' ? $value : null;
    }
}
