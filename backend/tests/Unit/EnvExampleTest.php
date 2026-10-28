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
