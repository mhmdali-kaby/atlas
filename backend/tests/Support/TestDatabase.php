<?php

declare(strict_types=1);

namespace Tests\Support;

use PDO;
use PDOException;
use RuntimeException;

/**
 * The per-checkout Postgres test database (ATL-29).
 *
 * Every checkout (worktree) gets its own database, `atlas_test_<first 8 hex of sha1(realpath of the
 * repository root)>`, so parallel test runs in different checkouts never reset each other's schema.
 * `DB_TEST_DATABASE`, when set and non-empty, overrides the derived name.
 *
 * The same rule lives in Python in `atlas_shared.testing.database_name`; both sides must agree so
 * pytest reads the schema `php artisan test` migrated.
 */
final class TestDatabase
{
    public const PREFIX = 'atlas_test_';

    public const OVERRIDE_VAR = 'DB_TEST_DATABASE';

    public static function repoRoot(): string
    {
        $root = realpath(__DIR__.'/../../..');
        if ($root === false) {
            throw new RuntimeException('Cannot resolve the repository root from '.__DIR__);
        }

        return $root;
    }

    public static function derivedName(?string $repoRoot = null): string
    {
        return self::PREFIX.substr(sha1($repoRoot ?? self::repoRoot()), 0, 8);
    }

    public static function name(): string
    {
        $override = self::env(self::OVERRIDE_VAR);

        return $override !== null ? $override : self::derivedName();
    }

    /**
     * Points DB_DATABASE at the test database for this process and creates the database if missing.
     * Runs from the PHPUnit bootstrap, before any Laravel application boots.
     */
    public static function configure(): string
    {
        $name = self::name();
        foreach (['DB_DATABASE' => $name, 'DB_URL' => ''] as $key => $value) {
            putenv($key.'='.$value);
            $_ENV[$key] = $value;
            $_SERVER[$key] = $value;
        }

        self::createIfMissing($name);

        return $name;
    }

    private static function createIfMissing(string $name): void
    {
        if (preg_match('/^[a-z_][a-z0-9_]{0,62}$/', $name) !== 1) {
            throw new RuntimeException("Refusing to use test database name '{$name}': lowercase letters, digits and underscores only.");
        }

        $dsn = sprintf(
            'pgsql:host=%s;port=%s;dbname=postgres',
            self::env('DB_HOST') ?? '127.0.0.1',
            self::env('DB_PORT') ?? '5433',
        );
        $pdo = new PDO($dsn, self::env('DB_USERNAME') ?? 'atlas', self::env('DB_PASSWORD') ?? 'atlas', [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        ]);

        $exists = $pdo->prepare('SELECT 1 FROM pg_database WHERE datname = ?');
        $exists->execute([$name]);
        if ($exists->fetchColumn() !== false) {
            return;
        }

        try {
            $pdo->exec('CREATE DATABASE "'.$name.'"');
        } catch (PDOException $e) {
            // 42P04 duplicate_database: a concurrent run in the same checkout created it first.
            if ($e->getCode() !== '42P04') {
                throw $e;
            }
        }
    }

    private static function env(string $key): ?string
    {
        $value = $_SERVER[$key] ?? $_ENV[$key] ?? getenv($key);

        return is_string($value) && $value !== '' ? $value : null;
    }
}
