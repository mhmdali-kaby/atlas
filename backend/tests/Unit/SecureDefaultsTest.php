<?php

declare(strict_types=1);

use Tests\TestCase;

uses(TestCase::class);

/**
 * ATL-33 AC4: config fallbacks are safe when .env is missing. Each test unsets the real
 * process environment variable, then `require`s the actual config file (never `env()` in
 * isolation, which would just echo back whatever literal default the test itself passes
 * in) so a regression in config/session.php or config/database.php's fallback fails this test.
 */
function atl33RestoreEnv(string $name, string|false $previous): void
{
    if ($previous === false) {
        putenv($name);
        unset($_ENV[$name], $_SERVER[$name]);

        return;
    }

    putenv("{$name}={$previous}");
    $_ENV[$name] = $previous;
    $_SERVER[$name] = $previous;
}

it('falls back to a 15-minute, atlas-session-cookie session when SESSION_LIFETIME/SESSION_COOKIE are unset', function (): void {
    $previousLifetime = getenv('SESSION_LIFETIME');
    $previousCookie = getenv('SESSION_COOKIE');
    putenv('SESSION_LIFETIME');
    putenv('SESSION_COOKIE');
    unset($_ENV['SESSION_LIFETIME'], $_SERVER['SESSION_LIFETIME'], $_ENV['SESSION_COOKIE'], $_SERVER['SESSION_COOKIE']);

    try {
        /** @var array<string, mixed> $session */
        $session = require config_path('session.php');

        expect($session['lifetime'])->toBe(15)
            ->and($session['cookie'])->toBe('atlas-session');
    } finally {
        atl33RestoreEnv('SESSION_LIFETIME', $previousLifetime);
        atl33RestoreEnv('SESSION_COOKIE', $previousCookie);
    }
});

it('falls back to the pgsql connection when DB_CONNECTION is unset', function (): void {
    $previous = getenv('DB_CONNECTION');
    putenv('DB_CONNECTION');
    unset($_ENV['DB_CONNECTION'], $_SERVER['DB_CONNECTION']);

    try {
        /** @var array<string, mixed> $database */
        $database = require config_path('database.php');

        expect($database['default'])->toBe('pgsql');
    } finally {
        atl33RestoreEnv('DB_CONNECTION', $previous);
    }
});
