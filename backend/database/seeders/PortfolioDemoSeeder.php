<?php

declare(strict_types=1);

namespace Database\Seeders;

use Brick\Math\BigDecimal;
use Brick\Math\RoundingMode;
use Carbon\CarbonImmutable;
use Illuminate\Database\Seeder;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;
use RuntimeException;

/**
 * Realistic demo-account Portfolio data for tests, local development and Storybook fixtures:
 * a 5,000,000 USDT starting balance, 30 days of 15-minute equity points ending now, a
 * BTC-USDT-SWAP long and an XAU-USDT-SWAP short, trade-fee and funding bills, and a fresh
 * reconciled_at. Replaces any existing demo rows in those tables. Refuses to run in production.
 *
 * The figures are illustrative fixture data, not what a real demo account holds (decision #237).
 */
class PortfolioDemoSeeder extends Seeder
{
    public const ENV = 'demo';

    public const BASELINE_USD = '5000000';

    public const DAYS = 30;

    public const STEP_MINUTES = 15;

    private const TABLES = ['equity_curve', 'balances_snapshots', 'positions_snapshots', 'bills', 'deposits_withdrawals', 'sync_status'];

    public function run(): void
    {
        if (app()->environment('production')) {
            throw new RuntimeException('PortfolioDemoSeeder refuses to run when APP_ENV=production.');
        }

        $now = CarbonImmutable::now()->utc()->startOfMinute();

        DB::transaction(function () use ($now): void {
            foreach (self::TABLES as $table) {
                DB::table($table)->where('env', self::ENV)->delete();
            }

            $positions = $this->positions($now);
            $lastEquity = $this->equityCurve($now, $positions['upl_usd']);
            $this->balances($now, $lastEquity, $positions['upl_usd']);
            DB::table('positions_snapshots')->insert($positions['rows']);
            $this->bills($now);
            $this->syncStatus($now);
        });
    }

    /**
     * A deterministic integer random walk in cents from the baseline; the last point is
     * returned so the holdings add up to it.
     */
    private function equityCurve(CarbonImmutable $now, BigDecimal $upl): BigDecimal
    {
        $steps = intdiv(self::DAYS * 24 * 60, self::STEP_MINUTES);
        $cents = BigDecimal::of(self::BASELINE_USD)->multipliedBy(100)->toBigInteger()->toInt();
        $seed = 20261028;
        $rows = [];
        $last = BigDecimal::zero();

        for ($i = 0; $i <= $steps; $i++) {
            if ($i > 0) {
                $seed = ($seed * 1103515245 + 12345) % 2147483648;
                // Step in cents: −1,000.00 … +1,040.00 USD, a slight upward drift.
                $cents += ($seed % 204001) - 100000;
            }
            $ts = $now->subMinutes(($steps - $i) * self::STEP_MINUTES);
            $last = BigDecimal::ofUnscaledValue($cents, 2);
            $rows[] = [
                'id' => (string) Str::uuid(),
                'env' => self::ENV,
                'ts' => self::t($ts),
                'total_eq_usd' => (string) $last,
                'upl_usd' => $i === $steps ? (string) $upl : null,
                'created_at' => self::t($now),
                'updated_at' => self::t($now),
            ];
        }

        foreach (array_chunk($rows, 500) as $chunk) {
            DB::table('equity_curve')->insert($chunk);
        }

        return $last;
    }

    private function balances(CarbonImmutable $now, BigDecimal $equity, BigDecimal $upl): void
    {
        $usdc = BigDecimal::of('25000');
        $funding = BigDecimal::of('12000');
        $usdt = $equity->minus($usdc)->minus($funding);

        $rows = [
            ['ccy' => 'USDT', 'account' => 'trading', 'eq' => $usdt, 'avail' => $usdt->minus('7018.258333'), 'frozen' => BigDecimal::of('7018.258333'), 'upl' => $upl],
            ['ccy' => 'USDC', 'account' => 'trading', 'eq' => $usdc, 'avail' => $usdc, 'frozen' => BigDecimal::zero(), 'upl' => BigDecimal::zero()],
            ['ccy' => 'USDT', 'account' => 'funding', 'eq' => $funding, 'avail' => $funding, 'frozen' => BigDecimal::zero(), 'upl' => null],
        ];

        DB::table('balances_snapshots')->insert(array_map(fn (array $r): array => [
            'id' => (string) Str::uuid(),
            'env' => self::ENV,
            'ccy' => $r['ccy'],
            'account' => $r['account'],
            'eq' => (string) $r['eq'],
            'avail_bal' => (string) $r['avail'],
            'frozen_bal' => (string) $r['frozen'],
            'eq_usd' => (string) $r['eq'],
            'upl' => $r['upl'] === null ? null : (string) $r['upl'],
            'taken_at' => self::t($now),
            'created_at' => self::t($now),
            'updated_at' => self::t($now),
        ], $rows));
    }

    /**
     * BTC long 25 contracts (ctVal 0.01 BTC) at 5x; XAU short 400 contracts (ctVal 0.01 oz) at 3x.
     * Net position mode: the sign of `pos` is the side.
     *
     * @return array{rows: list<array<string, mixed>>, upl_usd: BigDecimal}
     */
    private function positions(CarbonImmutable $now): array
    {
        $specs = [
            ['inst_id' => 'BTC-USDT-SWAP', 'pos' => '25', 'ct_val' => '0.01', 'avg' => '67250.5', 'mark' => '68120.3', 'liq' => '54112.8', 'lever' => '5', 'opened' => $now->subHours(9)],
            ['inst_id' => 'XAU-USDT-SWAP', 'pos' => '-400', 'ct_val' => '0.01', 'avg' => '2741.8', 'mark' => '2728.4', 'liq' => '3618.9', 'lever' => '3', 'opened' => $now->subHours(27)],
        ];

        $total = BigDecimal::zero();
        $rows = [];
        foreach ($specs as $s) {
            $qty = BigDecimal::of($s['pos'])->multipliedBy($s['ct_val']);
            $upl = BigDecimal::of($s['mark'])->minus($s['avg'])->multipliedBy($qty);
            $margin = BigDecimal::of($s['avg'])->multipliedBy($qty->abs())->dividedBy($s['lever'], 6, RoundingMode::HalfUp);
            $total = $total->plus($upl);
            $rows[] = [
                'id' => (string) Str::uuid(),
                'env' => self::ENV,
                'inst_id' => $s['inst_id'],
                'pos_side' => 'net',
                'pos' => $s['pos'],
                'avg_px' => $s['avg'],
                'mark_px' => $s['mark'],
                'liq_px' => $s['liq'],
                'lever' => $s['lever'],
                'upl' => (string) $upl,
                'upl_ratio' => (string) $upl->dividedBy($margin, 8, RoundingMode::HalfUp),
                'margin' => (string) $margin,
                'mgn_mode' => 'isolated',
                'c_time' => self::t($s['opened']),
                'taken_at' => self::t($now),
                'created_at' => self::t($now),
                'updated_at' => self::t($now),
            ];
        }

        return ['rows' => $rows, 'upl_usd' => $total];
    }

    /** Opening trade fees (type 2) and funding settlements every 8 h (type 8). */
    private function bills(CarbonImmutable $now): void
    {
        $bills = [
            ['inst' => 'XAU-USDT-SWAP', 'type' => '2', 'sub' => '2', 'bal' => '-5.48360', 'fee' => '-5.48360', 'ts' => $now->subHours(27)],
            ['inst' => 'BTC-USDT-SWAP', 'type' => '2', 'sub' => '1', 'bal' => '-8.40631', 'fee' => '-8.40631', 'ts' => $now->subHours(9)],
            ['inst' => 'XAU-USDT-SWAP', 'type' => '8', 'sub' => '174', 'bal' => '1.096720', 'fee' => '0', 'ts' => $now->subHours(24)],
            ['inst' => 'XAU-USDT-SWAP', 'type' => '8', 'sub' => '174', 'bal' => '0.822540', 'fee' => '0', 'ts' => $now->subHours(16)],
            ['inst' => 'XAU-USDT-SWAP', 'type' => '8', 'sub' => '173', 'bal' => '-0.274180', 'fee' => '0', 'ts' => $now->subHours(8)],
            ['inst' => 'BTC-USDT-SWAP', 'type' => '8', 'sub' => '173', 'bal' => '-1.681263', 'fee' => '0', 'ts' => $now->subHours(8)],
        ];

        $i = 0;
        DB::table('bills')->insert(array_map(function (array $b) use ($now, &$i): array {
            $i++;

            return [
                'id' => (string) Str::uuid(),
                'env' => self::ENV,
                'bill_id' => 'demo-seed-'.$i,
                'inst_id' => $b['inst'],
                'ccy' => 'USDT',
                'type' => $b['type'],
                'sub_type' => $b['sub'],
                'bal_chg' => $b['bal'],
                'pnl' => '0',
                'fee' => $b['fee'],
                'ts' => self::t($b['ts']),
                'created_at' => self::t($now),
                'updated_at' => self::t($now),
            ];
        }, $bills));
    }

    /** Timestamp literal with an explicit offset, independent of the DB session time zone. */
    private static function t(CarbonImmutable $ts): string
    {
        return $ts->utc()->format('Y-m-d H:i:s.uP');
    }

    private function syncStatus(CarbonImmutable $now): void
    {
        DB::table('sync_status')->insert(array_map(fn (string $stream): array => [
            'id' => (string) Str::uuid(),
            'env' => self::ENV,
            'stream' => $stream,
            'last_success_at' => self::t($now),
            'last_error' => null,
            'reconciled_at' => self::t($now),
            'created_at' => self::t($now),
            'updated_at' => self::t($now),
        ], ['balances', 'positions', 'bills', 'cashflows']));
    }
}
