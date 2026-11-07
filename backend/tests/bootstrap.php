<?php

declare(strict_types=1);

use Tests\Support\TestDatabase;
use Tests\Support\TestRedis;

require __DIR__.'/../vendor/autoload.php';

// ATL-29: each checkout tests against its own Postgres database (created here if missing).
$atlasTestDatabase = TestDatabase::configure();
fwrite(STDERR, "Atlas test database: {$atlasTestDatabase}\n");

// ATL-211: each checkout's event-bus streams get their own Redis key prefix.
$atlasRedisPrefix = TestRedis::configure();
fwrite(STDERR, "Atlas test Redis prefix: {$atlasRedisPrefix}\n");
