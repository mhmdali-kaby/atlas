<?php

declare(strict_types=1);

/*
 * ATL-262 (security round 20, low severity): PortfolioDemoSeeder, WarroomDemoSeeder,
 * BackfillDemoSeeder and ResearchDemoSeeder only refused to run with APP_ENV=production. An owner
 * install normally runs APP_ENV=local, so any of these could be pointed at the owner's real
 * database by mistake and overwrite it with fixture data. They now also refuse when the database
 * already looks like a real install — a verified exchange connection, or any equity history — the
 * same way E2eSeeder already refuses by database name. ATLAS_DEMO_SEED=1 opts back in.
 */

use App\Models\Connection;
use Database\Seeders\BackfillDemoSeeder;
use Database\Seeders\PortfolioDemoSeeder;
use Database\Seeders\ResearchDemoSeeder;
use Database\Seeders\WarroomDemoSeeder;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

dataset('demo seeders', [
    'PortfolioDemoSeeder' => [PortfolioDemoSeeder::class, 'PortfolioDemoSeeder'],
    'WarroomDemoSeeder' => [WarroomDemoSeeder::class, 'WarroomDemoSeeder'],
    'BackfillDemoSeeder' => [BackfillDemoSeeder::class, 'BackfillDemoSeeder'],
    'ResearchDemoSeeder' => [ResearchDemoSeeder::class, 'ResearchDemoSeeder'],
]);

function atl262VerifiedConnection(): Connection
{
    return Connection::query()->create([
        'exchange' => 'okx',
        'env' => 'demo',
        'label' => 'OKX Demo',
        'api_key' => 'owner-real-key',
        'api_secret' => 'owner-real-secret',
        'passphrase' => 'owner-real-passphrase',
        'last_verified_at' => now(),
    ]);
}

it('refuses to run with a verified connection present, leaving agent_scores and equity_curve unchanged', function (string $class, string $name): void {
    atl262VerifiedConnection();

    expect(fn () => (new $class)->run())
        ->toThrow(RuntimeException::class, "{$name} refuses to run");

    expect(DB::table('agent_scores')->count())->toBe(0)
        ->and(DB::table('equity_curve')->count())->toBe(0);
})->with('demo seeders');

it('refuses to run with an existing equity_curve row present', function (string $class, string $name): void {
    DB::table('equity_curve')->insert([
        'id' => (string) Str::uuid(),
        'env' => 'demo',
        'ts' => now(),
        'total_eq_usd' => '1000.00',
        'upl_usd' => null,
        'created_at' => now(),
        'updated_at' => now(),
    ]);

    expect(fn () => (new $class)->run())
        ->toThrow(RuntimeException::class, "{$name} refuses to run");
})->with('demo seeders');

it('seeds normally with no verified connection and no equity history', function (string $class): void {
    expect(fn () => (new $class)->run())->not->toThrow(RuntimeException::class);
})->with('demo seeders');

it('seeds a database with a verified connection when ATLAS_DEMO_SEED=1 is set', function (string $class): void {
    atl262VerifiedConnection();
    config(['services.demo_seed.enabled' => true]);

    expect(fn () => (new $class)->run())->not->toThrow(RuntimeException::class);
})->with('demo seeders');

it('still refuses to run in production even with ATLAS_DEMO_SEED=1', function (string $class, string $name): void {
    config(['services.demo_seed.enabled' => true]);
    $this->app['env'] = 'production';

    expect(fn () => (new $class)->run())
        ->toThrow(RuntimeException::class, "{$name} refuses to run when APP_ENV=production");
})->with('demo seeders');
