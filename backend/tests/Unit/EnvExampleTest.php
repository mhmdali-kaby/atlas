<?php

declare(strict_types=1);

use Dotenv\Dotenv;

/*
 * Security round 1, low severity (ATL-32): the env file the owner's install starts from must not
 * ship a 2-hour server session or debug-mode stack traces. Parses the file directly rather than
 * trusting config() during the test run, since phpunit.xml does not pin either value and a
 * developer's own .env can differ from the shipped example.
 */
it('ships a session lifetime of 15 minutes or less in the shipped backend env example', function (): void {
    $path = dirname(__DIR__, 2).'/.env.example';
    $values = Dotenv::parse((string) file_get_contents($path));

    expect($values)->toHaveKey('SESSION_LIFETIME');
    expect((int) $values['SESSION_LIFETIME'])->toBeLessThanOrEqual(15);
});

it('ships APP_DEBUG=false in the shipped backend env example', function (): void {
    $path = dirname(__DIR__, 2).'/.env.example';
    $values = Dotenv::parse((string) file_get_contents($path));

    expect($values)->toHaveKey('APP_DEBUG');
    expect($values['APP_DEBUG'])->toBe('false');
});

/*
 * ATL-50 (security round 4, finding 1): the example shipped an empty REVERB_APP_SECRET and a
 * Reverb server bound to every interface. `./atlas up` generates the secret into backend/.env on
 * first run (so the example itself must stay empty, never a real shared value), and the server
 * host must default to 127.0.0.1 rather than rely on an unset env var happening to be safe.
 */
it('ships REVERB_APP_SECRET empty in the shipped backend env example', function (): void {
    $path = dirname(__DIR__, 2).'/.env.example';
    $values = Dotenv::parse((string) file_get_contents($path));

    expect($values)->toHaveKey('REVERB_APP_SECRET');
    expect($values['REVERB_APP_SECRET'])->toBe('');
});

it('ships REVERB_SERVER_HOST=127.0.0.1 in the shipped backend env example', function (): void {
    $path = dirname(__DIR__, 2).'/.env.example';
    $values = Dotenv::parse((string) file_get_contents($path));

    expect($values)->toHaveKey('REVERB_SERVER_HOST');
    expect($values['REVERB_SERVER_HOST'])->toBe('127.0.0.1');
});
