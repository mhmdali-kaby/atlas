<?php

declare(strict_types=1);

namespace Database\Seeders\Concerns;

use App\Models\Connection;
use Database\Seeders\E2eSeeder;
use Illuminate\Support\Facades\DB;
use RuntimeException;

/**
 * Shared production/real-install guard for the demo seeders (PortfolioDemoSeeder,
 * WarroomDemoSeeder, BackfillDemoSeeder, ResearchDemoSeeder).
 *
 * Besides refusing APP_ENV=production, a demo seeder must not overwrite an owner's real install
 * that happens to sit on a non-production APP_ENV (local, staging): a verified exchange
 * connection, or any equity history, means OKX has actually been synced, so this is not an empty
 * demo database. E2eSeeder already guards this the other way, by DB name: a database named
 * `atlas_e2e_*` can only ever be the throwaway Playwright database (E2eSeeder itself refuses any
 * other name), so these seeders trust that name the same way E2eSeeder does and skip the
 * real-install check there — otherwise E2eSeeder's own chain (Warroom, then Portfolio, which
 * writes equity_curve, then Backfill) would trip over its own earlier writes.
 *
 * Set ATLAS_DEMO_SEED=1 to seed anyway — the operator's explicit statement that this install is a
 * demo box, not the owner's (security round 20, ATL-262).
 */
trait GuardsRealInstall
{
    private function guardAgainstRealInstall(string $seederName): void
    {
        if (app()->environment('production')) {
            throw new RuntimeException("{$seederName} refuses to run when APP_ENV=production.");
        }

        if (config('services.demo_seed.enabled') === true) {
            return;
        }

        $database = (string) config('database.connections.'.config('database.default').'.database');
        if (str_starts_with($database, E2eSeeder::DATABASE_PREFIX)) {
            return;
        }

        $looksLikeRealInstall = Connection::query()->whereNotNull('last_verified_at')->exists()
            || DB::table('equity_curve')->exists();

        if ($looksLikeRealInstall) {
            throw new RuntimeException(
                "{$seederName} refuses to run: this database has a verified connection or equity history, so it looks like a real install, not an empty demo database. Set ATLAS_DEMO_SEED=1 to seed it anyway."
            );
        }
    }
}
