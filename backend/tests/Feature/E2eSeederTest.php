<?php

declare(strict_types=1);

use App\Models\User;
use Database\Seeders\E2eSeeder;
use Illuminate\Support\Facades\DB;

it('refuses to seed a database not named atlas_e2e_*', function (): void {
    // The Pest database is atlas_test_<hash>, so the guard must refuse it.
    expect(fn () => app(E2eSeeder::class)->run())
        ->toThrow(RuntimeException::class, 'only seeds an atlas_e2e_* database');

    expect(User::query()->count())->toBe(0);
});

it('refuses to run in production', function (): void {
    $this->app['env'] = 'production';

    expect(fn () => app(E2eSeeder::class)->run())
        ->toThrow(RuntimeException::class, 'refuses to run when APP_ENV=production');
});

/*
 * ATL-262 round 1 review: E2eSeeder calls WarroomDemoSeeder, then PortfolioDemoSeeder (which
 * writes equity_curve), then BackfillDemoSeeder in sequence. Each of those three now has its own
 * real-install guard (GuardsRealInstall), so without an exemption Backfill would trip over the
 * equity_curve rows Portfolio just wrote in the same run. A database named atlas_e2e_* is only
 * ever E2eSeeder's own throwaway database (E2eSeeder itself refuses any other name), so
 * GuardsRealInstall trusts that name the same way and skips the real-install check for it.
 */
it('seeds the full E2eSeeder chain under an atlas_e2e_* database name without the real-install guard tripping on its own writes', function (): void {
    $connection = config('database.default');
    config(["database.connections.{$connection}.database" => E2eSeeder::DATABASE_PREFIX.'atl262_probe']);

    expect(fn () => app(E2eSeeder::class)->run())->not->toThrow(RuntimeException::class);

    expect(User::query()->where('email', E2eSeeder::EMAIL)->exists())->toBeTrue()
        ->and(DB::table('warroom_messages')->count())->toBeGreaterThan(0)
        ->and(DB::table('equity_curve')->where('env', 'demo')->count())->toBeGreaterThan(0)
        ->and(DB::table('backfill_jobs')->where('env', 'demo')->count())->toBeGreaterThan(0);
});
