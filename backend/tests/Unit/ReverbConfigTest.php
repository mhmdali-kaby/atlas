<?php

declare(strict_types=1);

use Tests\TestCase;

uses(TestCase::class);

/**
 * ATL-50 (security round 4, finding 1): Reverb shipped with `allowed_origins => ['*']` and a
 * server host of `0.0.0.0`, so any web page the owner visited could open a WebSocket to it. These
 * tests `require` the actual config file with the real env unset (never `env()` in isolation,
 * which would just echo back whatever literal default the test passes in), so a regression in
 * config/reverb.php's fallback fails this test. See Tests\Unit\SecureDefaultsTest for the pattern.
 */
function atl50UnsetEnv(string $name): string|false
{
    $previous = getenv($name);
    putenv($name);
    unset($_ENV[$name], $_SERVER[$name]);

    return $previous;
}

function atl50RestoreEnv(string $name, string|false $previous): void
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

/**
 * @return array<string, mixed>
 */
function atl50ReverbConfig(): array
{
    /** @var array<string, mixed> $config */
    $config = require config_path('reverb.php');

    return $config;
}

it('binds the Reverb server to 127.0.0.1 by default, not 0.0.0.0', function (): void {
    $previous = atl50UnsetEnv('REVERB_SERVER_HOST');

    try {
        expect(atl50ReverbConfig()['servers']['reverb']['host'])->toBe('127.0.0.1');
    } finally {
        atl50RestoreEnv('REVERB_SERVER_HOST', $previous);
    }
});

it('never allows every origin, with or without FRONTEND_URL set', function (): void {
    $previous = atl50UnsetEnv('FRONTEND_URL');

    try {
        $origins = atl50ReverbConfig()['apps']['apps'][0]['allowed_origins'];
        expect($origins)->not->toContain('*')->not->toBeEmpty();
    } finally {
        atl50RestoreEnv('FRONTEND_URL', $previous);
    }
});

it('restricts allowed_origins to the FRONTEND_URL host', function (): void {
    $previous = atl50UnsetEnv('FRONTEND_URL');
    putenv('FRONTEND_URL=http://localhost:3000');
    $_ENV['FRONTEND_URL'] = 'http://localhost:3000';
    $_SERVER['FRONTEND_URL'] = 'http://localhost:3000';

    try {
        $origins = atl50ReverbConfig()['apps']['apps'][0]['allowed_origins'];
        expect($origins)->toBe(['localhost']);
    } finally {
        atl50RestoreEnv('FRONTEND_URL', $previous);
    }
});

it('rejects client events by default', function (): void {
    $previous = atl50UnsetEnv('REVERB_APP_ACCEPT_CLIENT_EVENTS_FROM');

    try {
        expect(atl50ReverbConfig()['apps']['apps'][0]['accept_client_events_from'])->toBe('none');
    } finally {
        atl50RestoreEnv('REVERB_APP_ACCEPT_CLIENT_EVENTS_FROM', $previous);
    }
});
