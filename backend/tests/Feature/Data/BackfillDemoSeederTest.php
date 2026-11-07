<?php

declare(strict_types=1);

use App\Models\User;
use Database\Seeders\BackfillDemoSeeder;
use Illuminate\Support\Facades\DB;

/*
 * ATL-49: the Settings → Data e2e fixture. The page's Playwright flow reads it through the Data API
 * and re-runs it to keep the running jobs fresh, so it must be idempotent.
 */

it('seeds eight instruments with jobs in every state and one issue of each kind, idempotently', function (): void {
    $this->seed(BackfillDemoSeeder::class);
    $this->seed(BackfillDemoSeeder::class);

    expect(DB::table('instruments')->where('env', 'demo')->count())->toBe(8)
        ->and(DB::table('backfill_jobs')->where('env', 'demo')->count())->toBe(8 * 7 + 3)
        ->and(DB::table('data_quality_issues')->where('env', 'demo')->count())->toBe(5);

    $statuses = DB::table('backfill_jobs')->distinct()->pluck('status')->sort()->values()->all();
    expect($statuses)->toBe(['done', 'failed', 'pending', 'running']);

    $user = User::factory()->create();
    $data = $this->actingAs($user)->getJson('/api/data/backfill?env=demo')->assertOk()->json('data');
    $ids = collect($data['instruments'])->pluck('inst_id')->all();
    expect($ids)->toContain(...array_keys(BackfillDemoSeeder::INSTRUMENTS))
        ->and($ids)->toContain('BTC', 'ETH', 'SOL')
        ->and(collect($data['quality_issues'])->pluck('kind')->unique()->sort()->values()->all())
        ->toBe(['duplicate', 'gap', 'outlier', 'zero_volume']);

    $xcu = collect($data['instruments'])->firstWhere('inst_id', 'XCU-USDT-SWAP');
    expect($xcu['asset_class'])->toBe('commodity')
        ->and(collect($xcu['datasets'])->firstWhere('dataset', 'candles:1H')['status'])->toBe('failed');
});
