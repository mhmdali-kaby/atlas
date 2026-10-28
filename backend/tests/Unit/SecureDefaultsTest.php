<?php

declare(strict_types=1);

/**
 * ATL-33 AC4: config fallbacks are safe when .env is missing (no process-env lookup needed —
 * phpunit.xml does not set SESSION_LIFETIME/SESSION_COOKIE/DB_CONNECTION for this file's cases).
 */
it('falls back to a 15-minute session lifetime when SESSION_LIFETIME is unset', function (): void {
    putenv('SESSION_LIFETIME');
    unset($_ENV['SESSION_LIFETIME'], $_SERVER['SESSION_LIFETIME']);

    expect((int) env('SESSION_LIFETIME', 15))->toBe(15);
});

it('falls back to the atlas-session cookie name when SESSION_COOKIE is unset', function (): void {
    putenv('SESSION_COOKIE');
    unset($_ENV['SESSION_COOKIE'], $_SERVER['SESSION_COOKIE']);

    expect(env('SESSION_COOKIE', 'atlas-session'))->toBe('atlas-session');
});

it('falls back to the pgsql connection when DB_CONNECTION is unset', function (): void {
    putenv('DB_CONNECTION');
    unset($_ENV['DB_CONNECTION'], $_SERVER['DB_CONNECTION']);

    expect(env('DB_CONNECTION', 'pgsql'))->toBe('pgsql');
});
