<?php

declare(strict_types=1);

use App\Support\Events\EventBus;
use Symfony\Component\Process\ExecutableFinder;
use Symfony\Component\Process\Process;
use Tests\Support\TestDatabase;
use Tests\Support\TestRedis;

/**
 * ATL-211: the per-checkout Redis key prefix for the event-bus streams, mirroring
 * atlas_shared.testing.redis_prefix / atlas_shared.bus on the Python side (decision #252).
 */
it('derives test_ plus the first 8 hex of sha1 of the repository root, overridden by REDIS_TEST_PREFIX', function (): void {
    expect(TestRedis::derivedPrefix('/Users/owner/atlas'))
        ->toBe('test_'.substr(sha1('/Users/owner/atlas'), 0, 8).':')
        ->and(TestRedis::derivedPrefix())->toMatch('/^test_[0-9a-f]{8}:$/')
        ->and(TestRedis::derivedPrefix('/a'))->not->toBe(TestRedis::derivedPrefix('/b'));

    // TestRedis::env() checks $_SERVER and $_ENV before getenv() (exactly like TestDatabase),
    // so the override has to be set the same way TestRedis::configure() itself sets it.
    $var = TestRedis::OVERRIDE_VAR;
    $originalServer = $_SERVER[$var] ?? null;
    $originalEnv = $_ENV[$var] ?? null;
    $originalGetenv = getenv($var);
    $set = function (string $value) use ($var): void {
        putenv($var.'='.$value);
        $_ENV[$var] = $value;
        $_SERVER[$var] = $value;
    };
    try {
        $set('ci_one:');
        expect(TestRedis::prefix())->toBe('ci_one:');

        // An empty override is ignored, like Python's atlas_shared.testing.redis_prefix.
        $set('');
        expect(TestRedis::prefix())->toBe(TestRedis::derivedPrefix());
    } finally {
        unset($_SERVER[$var], $_ENV[$var]);
        if ($originalServer !== null) {
            $_SERVER[$var] = $originalServer;
        }
        if ($originalEnv !== null) {
            $_ENV[$var] = $originalEnv;
        }
        putenv($originalGetenv === false ? $var : $var.'='.$originalGetenv);
    }
});

it('derives the same 8 hex as the Postgres test database\'s own derived name', function (): void {
    $hex = substr(TestDatabase::derivedName(), strlen(TestDatabase::PREFIX));
    expect(TestRedis::derivedPrefix())->toBe('test_'.$hex.':');
});

/**
 * The two languages hand-duplicate this formula (one in `atlas_shared.testing.redis_prefix`, one
 * in `Tests\Support\TestRedis`); a typo in either would silently desync which physical key each
 * side reads. Shelling into the real `uv run python` is what actually proves they still agree,
 * not just that each unit-tests its own copy of the formula.
 */
it('agrees with atlas_shared.testing.redis_prefix() on this checkout\'s prefix', function (): void {
    if ((new ExecutableFinder)->find('uv') === null) {
        test()->markTestSkipped('uv is not on PATH');
    }

    $process = new Process(
        ['uv', 'run', 'python', '-c', 'from atlas_shared import testing; print(testing.redis_prefix())'],
        realpath(base_path('../services')),
    );
    $process->setTimeout(120);
    $process->run();

    expect($process->isSuccessful())->toBeTrue($process->getErrorOutput())
        ->and(trim($process->getOutput()))->toBe(TestRedis::prefix());
});

it('publishes to the prefixed key EventBus::streamKey() names, equal to the test prefix plus the stream', function (): void {
    foreach (EventBus::STREAMS as $stream) {
        expect(EventBus::streamKey($stream))->toBe(TestRedis::prefix().$stream);
    }
    expect(EventBus::logicalStream(TestRedis::prefix().'atlas.warroom'))->toBe('atlas.warroom');
});

/**
 * Production has no test bootstrap, so ATLAS_REDIS_PREFIX (and config('database.redis.events.
 * options.prefix')) is unset: streams stay the plain atlas.* names the brief and the Python
 * services expect.
 */
it('publishes to the plain atlas.warroom when no prefix is configured (production)', function (): void {
    config(['database.redis.events.options.prefix' => '']);

    expect(EventBus::keyPrefix())->toBe('')
        ->and(EventBus::streamKey('atlas.warroom'))->toBe('atlas.warroom')
        ->and(EventBus::streamKeys())->toBe(EventBus::STREAMS)
        ->and(EventBus::logicalStream('atlas.warroom'))->toBe('atlas.warroom');
});
