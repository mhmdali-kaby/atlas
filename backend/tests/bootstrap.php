<?php

declare(strict_types=1);

use Tests\Support\TestDatabase;

require __DIR__.'/../vendor/autoload.php';

// ATL-29: each checkout tests against its own Postgres database (created here if missing).
$atlasTestDatabase = TestDatabase::configure();
fwrite(STDERR, "Atlas test database: {$atlasTestDatabase}\n");
