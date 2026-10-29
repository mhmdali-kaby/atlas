<?php

declare(strict_types=1);

namespace Database\Seeders;

use App\Models\User;
use Illuminate\Database\Seeder;
use RuntimeException;

/**
 * The Playwright flows' starting state (ATL-25): one owner who has not onboarded yet (no connection,
 * no settings change) plus PortfolioDemoSeeder's Portfolio data, so the Portfolio pages have figures
 * to show once onboarding lands there, WarroomDemoSeeder's 500 War Room messages, and
 * BackfillDemoSeeder's Settings → Data backfill (ATL-49).
 *
 * Refuses to run in production, and refuses any database whose name does not start with
 * `atlas_e2e_`, so a mistyped DB_DATABASE can never seed a test owner into the owner's real data.
 */
class E2eSeeder extends Seeder
{
    public const DATABASE_PREFIX = 'atlas_e2e_';

    public const EMAIL = 'owner@atlas.e2e';

    public const NAME = 'Atlas E2E Owner';

    /** Test-only credential for a throwaway database, never a real account. */
    public const PASSWORD = 'e2e-owner-password-1';

    public function run(): void
    {
        if (app()->environment('production')) {
            throw new RuntimeException('E2eSeeder refuses to run when APP_ENV=production.');
        }

        $database = (string) config('database.connections.'.config('database.default').'.database');
        if (! str_starts_with($database, self::DATABASE_PREFIX)) {
            throw new RuntimeException('E2eSeeder only seeds an '.self::DATABASE_PREFIX."* database, not '{$database}'.");
        }

        User::query()->create([
            'name' => self::NAME,
            'email' => self::EMAIL,
            'password' => self::PASSWORD,
        ]);

        $this->call(WarroomThreadSeeder::class);
        $this->call(WarroomDemoSeeder::class);
        $this->call(PortfolioDemoSeeder::class);
        $this->call(BackfillDemoSeeder::class);
    }
}
