<?php

declare(strict_types=1);

/*
 * Creates the Playwright flows' database (ATL-25) if it is missing: `php tests/E2e/database.php`.
 * The name comes from DB_DATABASE and must start with `atlas_e2e_`, so the e2e setup's
 * `migrate:fresh` can never be pointed at the owner's `atlas` database.
 */

use Database\Seeders\E2eSeeder;
use Tests\Support\TestDatabase;

require __DIR__.'/../../vendor/autoload.php';

$name = getenv('DB_DATABASE');
if (! is_string($name) || ! str_starts_with($name, E2eSeeder::DATABASE_PREFIX)) {
    fwrite(STDERR, 'DB_DATABASE must start with '.E2eSeeder::DATABASE_PREFIX.PHP_EOL);
    exit(1);
}

TestDatabase::createIfMissing($name);
echo $name.PHP_EOL;
