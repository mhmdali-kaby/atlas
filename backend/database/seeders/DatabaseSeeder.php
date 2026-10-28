<?php

declare(strict_types=1);

namespace Database\Seeders;

use Illuminate\Database\Seeder;

class DatabaseSeeder extends Seeder
{
    /**
     * Atlas seeds no users and no credentials. The owner is created with
     * `php artisan atlas:create-owner`. Only production-safe reference data is seeded here.
     */
    public function run(): void
    {
        $this->call(WarroomThreadSeeder::class);
    }
}
