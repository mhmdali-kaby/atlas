<?php

declare(strict_types=1);

namespace Database\Seeders;

use App\Models\WarroomThread;
use Illuminate\Database\Seeder;

/**
 * Reference data, safe in production and idempotent: one `main` War Room thread per env.
 */
class WarroomThreadSeeder extends Seeder
{
    public function run(): void
    {
        /** @var list<string> $envs */
        $envs = config('atlas_defaults.env.allowed');

        foreach ($envs as $env) {
            WarroomThread::query()->firstOrCreate(
                ['env' => $env, 'kind' => 'main', 'key' => WarroomThread::MAIN_KEY],
                ['title' => 'Main'],
            );
        }
    }
}
