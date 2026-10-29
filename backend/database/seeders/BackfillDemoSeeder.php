<?php

declare(strict_types=1);

namespace Database\Seeders;

use Carbon\CarbonImmutable;
use Illuminate\Database\Seeder;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;
use RuntimeException;

/**
 * Settings → Data fixture (ATL-49): eight demo instruments across five asset classes with backfill
 * jobs in every state (done, running, pending, failed) and one quality issue of each kind, so the
 * page's Playwright flows have a running backfill to show. Upserts by natural key, so it can be run
 * again (the flows re-run it to keep the running jobs' updated_at fresh). Refuses production.
 *
 * Illustrative fixture data only; trading_core writes the real rows (ATL-46, ATL-47).
 */
class BackfillDemoSeeder extends Seeder
{
    public const ENV = 'demo';

    /** inst_id => [asset_class, inst_type, listed (ISO date)] */
    public const INSTRUMENTS = [
        'BTC-USDT-SWAP' => ['crypto', 'SWAP', '2019-12-11'],
        'ETH-USDT-SWAP' => ['crypto', 'SWAP', '2019-12-11'],
        'SOL-USDT-SWAP' => ['crypto', 'SWAP', '2021-06-01'],
        'NVDA-USDT-SWAP' => ['stock', 'SWAP', '2025-08-01'],
        'TSLA-USDT-SWAP' => ['stock', 'SWAP', '2025-08-01'],
        'SPY-USDT-SWAP' => ['etf', 'SWAP', '2025-09-01'],
        'XAU-USDT-SWAP' => ['commodity', 'SWAP', '2025-06-01'],
        'XCU-USDT-SWAP' => ['commodity', 'SWAP', '2025-10-01'],
    ];

    private const BAR_SECONDS = [
        'candles:1D' => 86400, 'candles:4H' => 14400, 'candles:1H' => 3600,
        'candles:15m' => 900, 'candles:5m' => 300, 'candles:1m' => 60,
    ];

    /**
     * Each instrument's jobs: dataset => [status, fraction of the expected bars written].
     * Missing candle datasets are pending at 0.
     */
    private const PLAN = [
        'BTC-USDT-SWAP' => ['candles:1D' => ['done', 1], 'candles:4H' => ['done', 1], 'candles:1H' => ['done', 1], 'candles:15m' => ['done', 1], 'candles:5m' => ['done', 1], 'candles:1m' => ['done', 1], 'funding' => ['done', 1]],
        'ETH-USDT-SWAP' => ['candles:1D' => ['done', 1], 'candles:4H' => ['done', 1], 'candles:1H' => ['done', 1], 'candles:15m' => ['running', 0.62], 'funding' => ['done', 1]],
        'SOL-USDT-SWAP' => ['candles:1D' => ['done', 1], 'candles:4H' => ['done', 1], 'candles:1H' => ['done', 1], 'funding' => ['pending', 0]],
        'NVDA-USDT-SWAP' => ['candles:1D' => ['done', 1], 'candles:4H' => ['done', 1], 'candles:1H' => ['running', 0.31], 'funding' => ['running', 0.4]],
        'TSLA-USDT-SWAP' => ['candles:1D' => ['done', 1], 'funding' => ['pending', 0]],
        'SPY-USDT-SWAP' => ['candles:1D' => ['done', 1], 'candles:4H' => ['done', 1], 'candles:1H' => ['done', 1], 'candles:15m' => ['done', 1], 'candles:5m' => ['done', 1], 'candles:1m' => ['done', 1], 'funding' => ['done', 1]],
        'XAU-USDT-SWAP' => ['candles:1D' => ['done', 1], 'candles:4H' => ['done', 1], 'candles:1H' => ['done', 1], 'candles:15m' => ['running', 0.86], 'funding' => ['done', 1]],
        'XCU-USDT-SWAP' => ['candles:1D' => ['done', 1], 'candles:4H' => ['done', 1], 'candles:1H' => ['failed', 0.31], 'funding' => ['done', 1]],
    ];

    /** The per-currency open-interest jobs (ATL-46: OKX rubik OI is per ccy, crypto only). */
    private const CCY_PLAN = [
        'BTC' => ['done', 1],
        'ETH' => ['running', 0.5],
        'SOL' => ['pending', 0],
    ];

    /** config/defaults.yaml backfill.depth_days for the candle datasets (null = full history). */
    private const DEPTH_DAYS = [
        'candles:1D' => null, 'candles:4H' => 730, 'candles:1H' => 730,
        'candles:15m' => 180, 'candles:5m' => 180, 'candles:1m' => 30,
    ];

    public function run(): void
    {
        if (app()->environment('production')) {
            throw new RuntimeException('BackfillDemoSeeder refuses to run when APP_ENV=production.');
        }

        $now = CarbonImmutable::now()->utc()->startOfMinute();
        $created = $now->subHour();

        DB::transaction(function () use ($now, $created): void {
            foreach (self::INSTRUMENTS as $instId => [$class, $type, $listed]) {
                $this->instrument($instId, $class, $type, $listed, $now);
            }
            foreach (self::INSTRUMENTS as $instId => [, , $listedOn]) {
                $listed = CarbonImmutable::parse($listedOn, 'UTC');
                $datasets = array_merge(array_fill_keys(array_keys(self::BAR_SECONDS), ['pending', 0]), self::PLAN[$instId]);
                foreach ($datasets as $dataset => [$status, $fraction]) {
                    $this->job($instId, $dataset, $status, $fraction, $listed, $now, $created);
                }
            }
            foreach (self::CCY_PLAN as $ccy => [$status, $fraction]) {
                $this->job($ccy, 'open_interest', $status, $fraction, CarbonImmutable::parse('2019-12-11', 'UTC'), $now, $created);
            }
            $this->issues($now);
        });
    }

    private function instrument(string $instId, string $class, string $type, string $listed, CarbonImmutable $now): void
    {
        [$base, $quote] = explode('-', $instId);
        $values = [
            'inst_type' => $type, 'base_ccy' => $base, 'quote_ccy' => $quote, 'settle_ccy' => $quote,
            'lot_sz' => '1', 'min_sz' => '1', 'tick_sz' => '0.1', 'state' => 'live',
            'list_time' => $listed.' 00:00:00+00', 'asset_class' => $class, 'last_seen_at' => $now, 'updated_at' => $now,
        ];
        DB::table('instruments')->upsert(
            [$values + ['id' => (string) Str::uuid(), 'env' => self::ENV, 'inst_id' => $instId, 'created_at' => $now]],
            ['env', 'inst_id'],
            array_keys($values),
        );
    }

    private function job(string $instId, string $dataset, string $status, int|float $fraction, CarbonImmutable $listed, CarbonImmutable $now, CarbonImmutable $created): void
    {
        $bar = self::BAR_SECONDS[$dataset] ?? null;
        $depth = self::DEPTH_DAYS[$dataset] ?? null;
        $floor = CarbonImmutable::parse('2009-01-01T00:00:00Z');
        $targetFrom = $depth === null ? $floor : $created->subDays($depth);
        $from = $targetFrom->greaterThan($listed) ? $targetFrom : $listed;
        if ($bar !== null) {
            $total = intdiv($created->getTimestamp() - $from->getTimestamp(), $bar);
            $rows = (int) floor($total * $fraction);
            $cursor = $rows > 0 ? $created->subSeconds($rows * $bar) : null;
        } else {
            // Funding settles every 8 h; open interest is a daily series.
            $step = $dataset === 'funding' ? 28800 : 86400;
            $total = intdiv($created->getTimestamp() - $listed->getTimestamp(), $step);
            $rows = (int) floor($total * $fraction);
            $cursor = $rows > 0 ? $created->subSeconds($rows * $step) : null;
        }

        $values = [
            'target_from' => $targetFrom, 'cursor_ts' => $cursor, 'rows_written' => $rows, 'status' => $status,
            'last_error' => $status === 'failed' ? 'OKX 50001: Service temporarily unavailable. Please try again later. (3 attempts)' : null,
            'updated_at' => $now,
        ];
        DB::table('backfill_jobs')->upsert(
            [$values + ['id' => (string) Str::uuid(), 'env' => self::ENV, 'inst_id' => $instId, 'dataset' => $dataset, 'created_at' => $created]],
            ['env', 'inst_id', 'dataset'],
            array_keys($values),
        );
    }

    private function issues(CarbonImmutable $now): void
    {
        $at = static fn (int $minutesAgo): CarbonImmutable => $now->subMinutes($minutesAgo);
        $rows = [
            ['XCU-USDT-SWAP', 'candles:1m', 'gap', $at(184), $at(181), $at(178), null, ['missing_bars' => 4, 'repair_attempts' => 1, 'still_missing' => 4]],
            ['NVDA-USDT-SWAP', 'candles:5m', 'gap', $at(605), $at(595), $at(590), $at(585), ['missing_bars' => 3, 'repair_attempts' => 1, 'still_missing' => 0]],
            ['ETH-USDT-SWAP', 'candles:1m', 'duplicate', $at(240), $at(240), $at(236), null, ['rows' => 2]],
            ['SOL-USDT-SWAP', 'candles:1H', 'outlier', $at(1500), $at(1500), $at(1440), null, ['sigma' => 9.4]],
            ['TSLA-USDT-SWAP', 'candles:1D', 'zero_volume', $at(4320), $at(2880), $at(2870), null, ['bars' => 3]],
        ];
        foreach ($rows as [$instId, $dataset, $kind, $from, $to, $detected, $repaired, $detail]) {
            // Times are relative to now, so drop this fixture's copy from an earlier run first.
            DB::table('data_quality_issues')
                ->where(['env' => self::ENV, 'inst_id' => $instId, 'dataset' => $dataset, 'kind' => $kind])
                ->where('from_ts', '<>', $from)
                ->delete();
            $values = [
                'to_ts' => $to, 'detected_at' => $detected, 'repaired_at' => $repaired,
                'detail' => json_encode($detail, JSON_THROW_ON_ERROR), 'updated_at' => $now,
            ];
            DB::table('data_quality_issues')->upsert(
                [$values + ['id' => (string) Str::uuid(), 'env' => self::ENV, 'inst_id' => $instId, 'dataset' => $dataset, 'kind' => $kind, 'from_ts' => $from, 'created_at' => $now]],
                ['env', 'inst_id', 'dataset', 'kind', 'from_ts'],
                array_keys($values),
            );
        }
    }
}
