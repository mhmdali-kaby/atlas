<?php

declare(strict_types=1);

namespace App\Support\Portfolio;

use Brick\Math\BigDecimal;
use Brick\Math\RoundingMode;
use Carbon\CarbonImmutable;
use Illuminate\Database\Query\JoinClause;
use Illuminate\Support\Facades\DB;

/**
 * Portfolio Performance (brief §13, ATL-174): trade statistics from positions_closed, drawdown and
 * daily Sharpe/Sortino from equity_curve, and PnL breakdowns with an Asia/Baghdad heatmap.
 *
 * All math is brick/math BigDecimal; every figure leaves as a decimal string. Each metric is
 * {value, sample_size, reason}: reason explains a null value.
 */
final class PerformanceService
{
    public const REASON_INSUFFICIENT_DATA = 'insufficient_data';

    public const REASON_NO_TRADES = 'no_trades';

    public const REASON_NO_LOSSES = 'no_losses';

    public const REASON_NO_WINS = 'no_wins';

    public const REASON_NO_R = 'no_r';

    public const REASON_ZERO_VOLATILITY = 'zero_volatility';

    public const REASON_NO_DOWNSIDE = 'no_downside';

    public function __construct(private readonly PortfolioService $portfolio) {}

    /**
     * @return array<string, mixed>
     */
    public function performance(string $env, string $range): array
    {
        $now = CarbonImmutable::now()->utc();
        $from = $this->portfolio->rangeStart($range, $now);

        $trades = $this->trades($env, $from, $now);

        return [
            'env' => $env,
            'range' => $range,
            'from' => $from?->toIso8601ZuluString('microsecond'),
            'to' => $now->toIso8601ZuluString('microsecond'),
            'timezone' => PortfolioService::OWNER_TIMEZONE,
            ...$this->tradeStats($trades['usd'], $trades['excluded']),
            'max_drawdown' => $this->maxDrawdown($env, $from, $now),
            ...$this->ratios($env, $from, $now),
            'breakdowns' => $this->breakdowns($trades['usd']),
        ];
    }

    /**
     * Closed positions in range. Only USD-settled rows (USDT/USDC/USD) are counted; coin-margined
     * PnL is in the coin and there is no FX conversion yet.
     *
     * @return array{usd: list<array{pnl: BigDecimal, r: ?BigDecimal, inst_id: string, asset_class: string, agent: ?string, closed_at: CarbonImmutable}>, excluded: int}
     */
    private function trades(string $env, ?CarbonImmutable $from, CarbonImmutable $now): array
    {
        $q = DB::table('positions_closed as c')
            ->leftJoin('proposals as p', 'p.id', '=', 'c.proposal_id')
            ->leftJoin('instruments as i', static function (JoinClause $j): void {
                $j->on('i.inst_id', '=', 'c.inst_id')->on('i.env', '=', 'c.env');
            })
            ->where('c.env', $env)
            ->where('c.closed_at', '<=', self::db($now))
            ->whereNotNull('c.realised_pnl')
            ->orderBy('c.closed_at')
            ->orderBy('c.id')
            ->select([
                'c.inst_id', 'i.asset_class', 'c.agent', 'c.side', 'c.ccy', 'c.entry_px', 'c.exit_px',
                'c.realised_pnl', 'c.closed_at', 'p.stop as proposal_stop', 'p.entry as proposal_entry',
            ]);
        if ($from !== null) {
            $q->where('c.closed_at', '>=', self::db($from));
        }

        $usd = [];
        $excluded = 0;
        foreach ($q->get() as $row) {
            $ccy = is_string($row->ccy) ? $row->ccy : null;
            $pnl = Money::of($row->realised_pnl);
            if ($pnl === null || ! Money::isUsdStable($ccy)) {
                $excluded++;

                continue;
            }
            $side = is_string($row->side) ? $row->side : null;
            $r = HistoryQuery::rMultiple(
                $side,
                Money::of($row->entry_px) ?? Money::of($row->proposal_entry),
                Money::of($row->exit_px),
                Money::of($row->proposal_stop),
            );
            $usd[] = [
                'pnl' => $pnl,
                'r' => $r === null ? null : BigDecimal::of($r),
                'inst_id' => is_string($row->inst_id) ? $row->inst_id : '',
                'asset_class' => is_string($row->asset_class) && $row->asset_class !== '' ? $row->asset_class : 'other',
                'agent' => is_string($row->agent) && $row->agent !== '' ? $row->agent : null,
                'closed_at' => CarbonImmutable::parse(is_string($row->closed_at) ? $row->closed_at : 'now')->utc(),
            ];
        }

        return ['usd' => $usd, 'excluded' => $excluded];
    }

    /**
     * @param  list<array{pnl: BigDecimal, r: ?BigDecimal, inst_id: string, asset_class: string, agent: ?string, closed_at: CarbonImmutable}>  $trades
     * @return array<string, mixed>
     */
    private function tradeStats(array $trades, int $excluded): array
    {
        $wins = 0;
        $losses = 0;
        $flats = 0;
        $grossProfit = BigDecimal::zero();
        $grossLoss = BigDecimal::zero();
        $rSum = BigDecimal::zero();
        $rCount = 0;
        foreach ($trades as $t) {
            if ($t['pnl']->isPositive()) {
                $wins++;
                $grossProfit = $grossProfit->plus($t['pnl']);
            } elseif ($t['pnl']->isNegative()) {
                $losses++;
                $grossLoss = $grossLoss->plus($t['pnl']);
            } else {
                $flats++;
            }
            if ($t['r'] !== null) {
                $rSum = $rSum->plus($t['r']);
                $rCount++;
            }
        }
        $decided = $wins + $losses;

        return [
            'trades' => [
                'total' => count($trades),
                'wins' => $wins,
                'losses' => $losses,
                'flats' => $flats,
                'excluded_non_usd' => $excluded,
                'gross_profit_usd' => Money::str($grossProfit),
                'gross_loss_usd' => Money::str($grossLoss),
                'net_pnl_usd' => Money::str($grossProfit->plus($grossLoss)),
            ],
            // Wins ÷ (wins + losses) × 100; flat trades are neither.
            'win_rate_pct' => $decided === 0
                ? self::metric(null, 0, self::REASON_NO_TRADES)
                : self::metric(self::fixed(BigDecimal::of($wins * 100)->dividedBy($decided, self::scale('pct_scale'), RoundingMode::HalfUp)), $decided),
            'avg_win_usd' => $wins === 0
                ? self::metric(null, 0, self::REASON_NO_WINS)
                : self::metric(Money::str($grossProfit->dividedBy($wins, self::scale('money_scale'), RoundingMode::HalfUp)), $wins),
            // Negative: the average losing trade's PnL.
            'avg_loss_usd' => $losses === 0
                ? self::metric(null, 0, self::REASON_NO_LOSSES)
                : self::metric(Money::str($grossLoss->dividedBy($losses, self::scale('money_scale'), RoundingMode::HalfUp)), $losses),
            // Gross profit ÷ |gross loss|.
            'profit_factor' => match (true) {
                $decided === 0 => self::metric(null, 0, self::REASON_NO_TRADES),
                $losses === 0 => self::metric(null, $decided, self::REASON_NO_LOSSES),
                default => self::metric(self::fixed($grossProfit->dividedBy($grossLoss->abs(), self::scale('ratio_scale'), RoundingMode::HalfUp)), $decided),
            },
            // Mean R over trades whose R is known (a proposal stop exists), R as the History API shows it.
            'expectancy_r' => $rCount === 0
                ? self::metric(null, 0, count($trades) === 0 ? self::REASON_NO_TRADES : self::REASON_NO_R)
                : self::metric(self::fixed($rSum->dividedBy($rCount, self::scale('ratio_scale'), RoundingMode::HalfUp)), $rCount),
        ];
    }

    /**
     * The deepest peak-to-trough fall of total equity (in %) inside the range, from every
     * equity_curve point. usd is that episode's peak − trough.
     *
     * @return array<string, mixed>
     */
    private function maxDrawdown(string $env, ?CarbonImmutable $from, CarbonImmutable $now): array
    {
        $base = DB::table('equity_curve')->where('env', $env)->where('ts', '<=', self::db($now));
        if ($from !== null) {
            $base->where('ts', '>=', self::db($from));
        }
        $n = (clone $base)->count();

        $peaks = (clone $base)->selectRaw(
            'ts, total_eq_usd, max(total_eq_usd) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak',
        );
        $withPeakTs = DB::query()->fromSub($peaks, 'e')
            ->where('peak', '>', 0)
            ->whereColumn('total_eq_usd', '<', 'peak')
            ->orderByRaw('(peak - total_eq_usd) / peak DESC')
            ->orderBy('ts')
            ->first(['ts', 'total_eq_usd', 'peak']);

        $empty = [
            'usd' => $n === 0 ? null : '0',
            'pct' => $n === 0 ? null : self::fixed(BigDecimal::zero()->toScale(self::scale('pct_scale'))),
            'peak_usd' => null, 'trough_usd' => null, 'peak_at' => null, 'trough_at' => null,
            'sample_size' => $n,
            'reason' => $n === 0 ? self::REASON_INSUFFICIENT_DATA : null,
        ];
        if ($withPeakTs === null) {
            return $empty;
        }

        $peak = Money::of($withPeakTs->peak) ?? BigDecimal::zero();
        $trough = Money::of($withPeakTs->total_eq_usd) ?? BigDecimal::zero();
        $troughTs = is_string($withPeakTs->ts) ? $withPeakTs->ts : 'now';
        $peakAt = (clone $base)->where('ts', '<=', self::db(CarbonImmutable::parse($troughTs)))
            ->where('total_eq_usd', $peak->toString())->orderByDesc('ts')->value('ts');
        $fall = $peak->minus($trough);

        return [
            'usd' => Money::str($fall),
            'pct' => self::fixed($fall->multipliedBy(100)->dividedBy($peak, self::scale('pct_scale'), RoundingMode::HalfUp)),
            'peak_usd' => Money::str($peak),
            'trough_usd' => Money::str($trough),
            'peak_at' => is_string($peakAt) ? CarbonImmutable::parse($peakAt)->utc()->toIso8601ZuluString('microsecond') : null,
            'trough_at' => CarbonImmutable::parse($troughTs)->utc()->toIso8601ZuluString('microsecond'),
            'sample_size' => $n,
            'reason' => null,
        ];
    }

    /**
     * Daily Sharpe and Sortino, annualised by √annualisation_days, risk-free 0. A day is an
     * Asia/Baghdad calendar day and its value the last equity point of that day. The daily
     * return is (E_d − E_{d−1} − net deposits on day d) ÷ E_{d−1}, so deposits are not profit.
     * Sharpe uses the sample standard deviation (n − 1); Sortino the downside deviation
     * √(Σ min(r, 0)² ÷ n). sample_size is the number of days of equity.
     *
     * @return array{sharpe: array<string, mixed>, sortino: array<string, mixed>}
     */
    private function ratios(string $env, ?CarbonImmutable $from, CarbonImmutable $now): array
    {
        $tz = PortfolioService::OWNER_TIMEZONE;
        $inner = DB::table('equity_curve')->where('env', $env)->where('ts', '<=', self::db($now))
            ->selectRaw('(ts AT TIME ZONE ?)::date AS day, ts, total_eq_usd', [$tz]);
        if ($from !== null) {
            $inner->where('ts', '>=', self::db($from));
        }
        $closes = DB::query()->fromSub($inner, 'e')
            ->selectRaw('DISTINCT ON (day) day, total_eq_usd')
            ->orderBy('day')->orderByDesc('ts')
            ->get();

        $days = count($closes);
        $minDays = self::scale('min_days_for_ratios');
        if ($days < max(2, $minDays)) {
            $null = self::metric(null, $days, self::REASON_INSUFFICIENT_DATA);

            return ['sharpe' => $null, 'sortino' => $null];
        }

        $flows = $this->dailyFlows($env, $from, $now);
        $calc = self::scale('calc_scale');
        $returns = [];
        $prev = null;
        foreach ($closes as $row) {
            $eq = Money::of($row->total_eq_usd) ?? BigDecimal::zero();
            $day = is_string($row->day) ? substr($row->day, 0, 10) : '';
            if ($prev !== null && $prev->isPositive()) {
                $flow = $flows[$day] ?? BigDecimal::zero();
                $returns[] = $eq->minus($prev)->minus($flow)->dividedBy($prev, $calc, RoundingMode::HalfUp);
            }
            $prev = $eq;
        }
        $n = count($returns);
        if ($n < 2) {
            $null = self::metric(null, $days, self::REASON_INSUFFICIENT_DATA);

            return ['sharpe' => $null, 'sortino' => $null];
        }

        $sum = BigDecimal::zero();
        foreach ($returns as $r) {
            $sum = $sum->plus($r);
        }
        $mean = $sum->dividedBy($n, $calc, RoundingMode::HalfUp);
        $sq = BigDecimal::zero();
        $down = BigDecimal::zero();
        foreach ($returns as $r) {
            $d = $r->minus($mean);
            $sq = $sq->plus($d->multipliedBy($d));
            if ($r->isNegative()) {
                $down = $down->plus($r->multipliedBy($r));
            }
        }
        $std = $sq->dividedBy($n - 1, $calc, RoundingMode::HalfUp)->sqrt($calc, RoundingMode::HalfUp);
        $downDev = $down->dividedBy($n, $calc, RoundingMode::HalfUp)->sqrt($calc, RoundingMode::HalfUp);
        $annual = BigDecimal::of(self::scale('annualisation_days'))->sqrt($calc, RoundingMode::HalfUp);
        $out = self::scale('ratio_scale');

        return [
            'sharpe' => $std->isZero()
                ? self::metric(null, $days, self::REASON_ZERO_VOLATILITY)
                : self::metric(self::fixed($mean->multipliedBy($annual)->dividedBy($std, $out, RoundingMode::HalfUp)), $days),
            'sortino' => $downDev->isZero()
                ? self::metric(null, $days, self::REASON_NO_DOWNSIDE)
                : self::metric(self::fixed($mean->multipliedBy($annual)->dividedBy($downDev, $out, RoundingMode::HalfUp)), $days),
        ];
    }

    /**
     * Net successful USD-stable deposits − withdrawals per Baghdad day.
     *
     * @return array<string, BigDecimal>
     */
    private function dailyFlows(string $env, ?CarbonImmutable $from, CarbonImmutable $now): array
    {
        $q = DB::table('deposits_withdrawals')
            ->where('env', $env)
            ->where('state', PortfolioService::CASHFLOW_SUCCESS_STATE)
            ->whereIn(DB::raw('upper(ccy)'), Money::USD_STABLE)
            ->where('ts', '<=', self::db($now))
            ->selectRaw("(ts AT TIME ZONE ?)::date AS day, sum(CASE WHEN kind = 'deposit' THEN amount ELSE -amount END) AS net", [PortfolioService::OWNER_TIMEZONE])
            ->groupBy('day');
        if ($from !== null) {
            $q->where('ts', '>=', self::db($from));
        }
        $out = [];
        foreach ($q->get() as $row) {
            $out[is_string($row->day) ? substr($row->day, 0, 10) : ''] = Money::of($row->net) ?? BigDecimal::zero();
        }

        return $out;
    }

    /**
     * PnL by instrument, asset class, agent, Baghdad weekday (ISO 1 = Monday) and Baghdad hour,
     * plus the weekday × hour heatmap (non-empty cells only).
     *
     * @param  list<array{pnl: BigDecimal, r: ?BigDecimal, inst_id: string, asset_class: string, agent: ?string, closed_at: CarbonImmutable}>  $trades
     * @return array<string, list<array<string, mixed>>>
     */
    private function breakdowns(array $trades): array
    {
        /** @var array<string, array<string, array{pnl: BigDecimal, trades: int, wins: int, losses: int}>> $groups */
        $groups = ['inst_id' => [], 'asset_class' => [], 'agent' => [], 'weekday' => [], 'hour' => [], 'cell' => []];
        foreach ($trades as $t) {
            $local = $t['closed_at']->setTimezone(PortfolioService::OWNER_TIMEZONE);
            $weekday = $local->isoWeekday();
            $hour = (int) $local->format('G');
            $keys = [
                'inst_id' => $t['inst_id'],
                'asset_class' => $t['asset_class'],
                'agent' => $t['agent'] ?? '',
                'weekday' => (string) $weekday,
                'hour' => (string) $hour,
                'cell' => $weekday.':'.$hour,
            ];
            foreach ($keys as $dim => $key) {
                $g = $groups[$dim][$key] ?? ['pnl' => BigDecimal::zero(), 'trades' => 0, 'wins' => 0, 'losses' => 0];
                $g['pnl'] = $g['pnl']->plus($t['pnl']);
                $g['trades']++;
                $g['wins'] += $t['pnl']->isPositive() ? 1 : 0;
                $g['losses'] += $t['pnl']->isNegative() ? 1 : 0;
                $groups[$dim][$key] = $g;
            }
        }

        $empty = ['pnl' => BigDecimal::zero(), 'trades' => 0, 'wins' => 0, 'losses' => 0];
        $byWeekday = [];
        for ($d = 1; $d <= 7; $d++) {
            $byWeekday[] = ['weekday' => $d, ...self::bucket($groups['weekday'][(string) $d] ?? $empty)];
        }
        $byHour = [];
        for ($h = 0; $h < 24; $h++) {
            $byHour[] = ['hour' => $h, ...self::bucket($groups['hour'][(string) $h] ?? $empty)];
        }
        $heatmap = [];
        foreach ($groups['cell'] as $key => $g) {
            [$d, $h] = array_map('intval', explode(':', (string) $key));
            $heatmap[] = ['weekday' => $d, 'hour' => $h, ...self::bucket($g)];
        }
        usort($heatmap, static fn (array $a, array $b): int => [$a['weekday'], $a['hour']] <=> [$b['weekday'], $b['hour']]);

        return [
            'by_instrument' => self::keyed($groups['inst_id'], 'inst_id'),
            'by_asset_class' => self::keyed($groups['asset_class'], 'asset_class'),
            'by_agent' => self::keyed($groups['agent'], 'agent'),
            'by_weekday' => $byWeekday,
            'by_hour' => $byHour,
            'heatmap' => $heatmap,
        ];
    }

    /**
     * Sorted by PnL, best first, then key.
     *
     * @param  array<string, array{pnl: BigDecimal, trades: int, wins: int, losses: int}>  $groups
     * @return list<array<string, mixed>>
     */
    private static function keyed(array $groups, string $name): array
    {
        $rows = [];
        foreach ($groups as $key => $g) {
            $rows[] = ['key' => (string) $key, 'pnl' => $g['pnl'], 'g' => $g];
        }
        usort($rows, static fn (array $a, array $b): int => $b['pnl']->compareTo($a['pnl']) ?: strcmp($a['key'], $b['key']));

        $out = [];
        foreach ($rows as $row) {
            // Agent '' = a position Atlas did not open (external).
            $out[] = [$name => $row['key'] === '' ? null : $row['key'], ...self::bucket($row['g'])];
        }

        return $out;
    }

    /**
     * @param  array{pnl: BigDecimal, trades: int, wins: int, losses: int}  $g
     * @return array{pnl_usd: ?string, trades: int, wins: int, losses: int}
     */
    private static function bucket(array $g): array
    {
        return ['pnl_usd' => Money::str($g['pnl']), 'trades' => $g['trades'], 'wins' => $g['wins'], 'losses' => $g['losses']];
    }

    /**
     * @return array{value: ?string, sample_size: int, reason: ?string}
     */
    private static function metric(?string $value, int $sampleSize, ?string $reason = null): array
    {
        return ['value' => $value, 'sample_size' => $sampleSize, 'reason' => $value === null ? $reason : null];
    }

    /** Fixed-scale string, never "-0.00". */
    private static function fixed(BigDecimal $v): string
    {
        return $v->isZero() ? $v->abs()->toString() : $v->toString();
    }

    private static function scale(string $key): int
    {
        $v = config('atlas_defaults.portfolio_performance.'.$key);

        return is_numeric($v) ? (int) $v : 0;
    }

    private static function db(CarbonImmutable $ts): string
    {
        return $ts->utc()->format('Y-m-d H:i:s.uP');
    }
}
