<?php

declare(strict_types=1);

namespace App\Support\Portfolio;

use Brick\Math\BigDecimal;
use Carbon\CarbonImmutable;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;

/**
 * Reads the OKX data that trading_core synced into Postgres (decision #235) and turns it into
 * the Portfolio figures (brief §13). Never calls OKX. All money is brick/math BigDecimal.
 */
final class PortfolioService
{
    /** OKX deposit-history / withdrawal-history `state` meaning "succeeded". */
    public const CASHFLOW_SUCCESS_STATE = '2';

    /** summary.stale is true when reconciled_at is older than this. */
    public const STALE_AFTER_SECONDS = 180;

    public const MAX_EQUITY_POINTS = 500;

    public const RANGES = ['1D', '1W', '1M', '3M', 'YTD', 'ALL'];

    public const OWNER_TIMEZONE = 'Asia/Baghdad';

    /**
     * @return array<string, string|bool|null>
     */
    public function summary(string $env): array
    {
        $latest = DB::table('equity_curve')->where('env', $env)->orderByDesc('ts')->first(['ts', 'total_eq_usd']);
        $baseline = DB::table('equity_curve')->where('env', $env)->orderBy('ts')->first(['ts', 'total_eq_usd']);

        $equity = Money::of($latest?->total_eq_usd);
        $netDeposits = $this->netDeposits($env, null);

        $totalPnl = null;
        $totalPnlPct = null;
        $pnlSince = null;
        if ($equity !== null && $baseline !== null) {
            $since = $this->utc($baseline->ts);
            $invested = Money::of($baseline->total_eq_usd)?->plus($this->netDeposits($env, $since)) ?? BigDecimal::zero();
            $totalPnl = $equity->minus($invested);
            $totalPnlPct = Money::pct($totalPnl, $invested);
            $pnlSince = $this->iso($since);
        }

        $todayPnl = null;
        if ($equity !== null) {
            $midnight = CarbonImmutable::now(self::OWNER_TIMEZONE)->startOfDay()->utc();
            $first = DB::table('equity_curve')->where('env', $env)->where('ts', '>=', $this->db($midnight))->orderBy('ts')->first(['total_eq_usd']);
            $todayPnl = $first === null ? null : $equity->minus(Money::of($first->total_eq_usd) ?? BigDecimal::zero());
        }

        $reconciledRaw = DB::table('sync_status')->where('env', $env)->max('reconciled_at');
        $reconciledAt = $reconciledRaw === null ? null : $this->utc($reconciledRaw);
        $stale = $reconciledAt === null
            || $reconciledAt->lt(CarbonImmutable::now()->subSeconds(self::STALE_AFTER_SECONDS));

        return [
            'env' => $env,
            'total_equity_usd' => Money::str($equity),
            'net_deposits_usd' => Money::str($netDeposits),
            'total_pnl_usd' => Money::str($totalPnl),
            'total_pnl_pct' => Money::str($totalPnlPct),
            'pnl_since' => $pnlSince,
            'today_pnl_usd' => Money::str($todayPnl),
            'unrealised_pnl_usd' => Money::str($this->unrealisedPnl($env)),
            'fees_paid_usd' => Money::str($this->feesPaid($env)),
            'funding_net_usd' => Money::str($this->fundingNet($env)),
            'equity_as_of' => $latest === null ? null : $this->iso($this->utc($latest->ts)),
            'reconciled_at' => $reconciledAt === null ? null : $this->iso($reconciledAt),
            'stale' => $stale,
        ];
    }

    /**
     * @return array{range: string, from: string|null, to: string, points: list<array{ts: string, equity_usd: string}>, markers: list<array<string, string|null>>}
     */
    public function equity(string $env, string $range): array
    {
        $now = CarbonImmutable::now()->utc();
        $from = $this->rangeStart($range, $now);

        $base = DB::table('equity_curve')->where('env', $env)->where('ts', '<=', $this->db($now));
        if ($from !== null) {
            $base->where('ts', '>=', $this->db($from));
        }

        /** @var object{n: int|string, first_ts: string|null, last_ts: string|null}|null $stats */
        $stats = (clone $base)->selectRaw('count(*) AS n, min(ts) AS first_ts, max(ts) AS last_ts')->first();
        $count = (int) ($stats->n ?? 0);

        if ($count <= self::MAX_EQUITY_POINTS || $stats?->first_ts === null || $stats->last_ts === null) {
            $rows = (clone $base)->orderBy('ts')->get(['ts', 'total_eq_usd']);
        } else {
            // Keep the last point of each time bucket. With width = ceil(span / (MAX − 1)) seconds,
            // the span covers at most MAX epoch-aligned buckets.
            $span = max(1, $this->utc($stats->last_ts)->getTimestamp() - $this->utc($stats->first_ts)->getTimestamp());
            $width = (int) ceil($span / (self::MAX_EQUITY_POINTS - 1));
            $inner = (clone $base)->selectRaw('ts, total_eq_usd, floor(extract(epoch from ts) / ?) AS bucket', [$width]);
            $rows = DB::query()
                ->fromSub(
                    DB::query()->fromSub($inner, 'e')->selectRaw('DISTINCT ON (bucket) ts, total_eq_usd')->orderBy('bucket')->orderByDesc('ts'),
                    'b',
                )
                ->orderBy('ts')
                ->get(['ts', 'total_eq_usd']);
        }

        $points = [];
        foreach ($rows as $row) {
            $points[] = [
                'ts' => $this->iso($this->utc($row->ts)),
                'equity_usd' => (string) Money::str(Money::of($row->total_eq_usd)),
            ];
        }

        $markersQuery = DB::table('deposits_withdrawals')->where('env', $env)
            ->where('state', self::CASHFLOW_SUCCESS_STATE)->where('ts', '<=', $this->db($now))->orderBy('ts');
        if ($from !== null) {
            $markersQuery->where('ts', '>=', $this->db($from));
        }
        $markers = [];
        foreach ($markersQuery->get() as $row) {
            $markers[] = $this->cashflow($row);
        }

        return [
            'range' => $range,
            'from' => $from === null ? null : $this->iso($from),
            'to' => $this->iso($now),
            'points' => $points,
            'markers' => $markers,
        ];
    }

    /**
     * Current balances, one row per (account, ccy), from each account's newest snapshot tick.
     *
     * @return array{taken_at: string|null, total_value_usd: string, items: list<array<string, string|null>>}
     */
    public function holdings(string $env): array
    {
        $rows = DB::table('balances_snapshots as b')
            ->where('b.env', $env)
            ->whereRaw('b.taken_at = (SELECT max(taken_at) FROM balances_snapshots m WHERE m.env = b.env AND m.account = b.account)')
            ->get();

        $total = BigDecimal::zero();
        $items = [];
        $takenAt = null;
        foreach ($rows as $row) {
            $quantity = Money::of($row->eq) ?? BigDecimal::zero();
            $value = Money::of($row->eq_usd) ?? (Money::isUsdStable($this->s($row->ccy)) ? $quantity : null);
            if ($value !== null) {
                $total = $total->plus($value);
            }
            $taken = $this->utc($row->taken_at);
            $takenAt = $takenAt === null || $taken->gt($takenAt) ? $taken : $takenAt;
            $items[] = ['row' => $row, 'quantity' => $quantity, 'value' => $value];
        }

        usort($items, static function (array $a, array $b): int {
            $va = $a['value'] ?? BigDecimal::zero();
            $vb = $b['value'] ?? BigDecimal::zero();

            return $vb->compareTo($va);
        });

        $out = [];
        foreach ($items as ['row' => $row, 'quantity' => $quantity, 'value' => $value]) {
            $out[] = [
                'ccy' => $this->s($row->ccy),
                'account' => $this->s($row->account),
                'quantity' => Money::str($quantity),
                'available' => Money::str(Money::of($row->avail_bal)),
                'frozen' => Money::str(Money::of($row->frozen_bal)),
                'value_usd' => Money::str($value),
                'pct_of_portfolio' => $value === null ? null : Money::str(Money::pct($value, $total)),
                'avg_cost_usd' => null,
                'change_24h_pct' => null,
            ];
        }

        return [
            'taken_at' => $takenAt === null ? null : $this->iso($takenAt),
            'total_value_usd' => (string) Money::str($total),
            'items' => $out,
        ];
    }

    /**
     * Open positions from the newest positions snapshot tick.
     *
     * @return array{taken_at: string|null, items: list<array<string, string|null>>}
     */
    public function positions(string $env): array
    {
        $takenAt = DB::table('positions_snapshots')->where('env', $env)->max('taken_at');
        if ($takenAt === null) {
            return ['taken_at' => null, 'items' => []];
        }

        $rows = DB::table('positions_snapshots as p')
            ->leftJoin('instruments as i', function ($join): void {
                $join->on('i.env', '=', 'p.env')->on('i.inst_id', '=', 'p.inst_id');
            })
            ->where('p.env', $env)
            ->where('p.taken_at', $takenAt)
            ->where('p.pos', '<>', 0)
            ->orderBy('p.inst_id')
            ->get(['p.*', 'i.settle_ccy']);

        $items = [];
        foreach ($rows as $row) {
            $pos = Money::of($row->pos) ?? BigDecimal::zero();
            $posSide = $this->s($row->pos_side);
            $side = match (true) {
                $posSide === 'long', $posSide === 'short' => $posSide,
                default => $pos->isNegative() ? 'short' : 'long',
            };
            $upl = Money::of($row->upl);
            $ratio = Money::of($row->upl_ratio);

            $items[] = [
                'id' => $this->s($row->id),
                'inst_id' => $this->s($row->inst_id),
                'side' => $side,
                'pos_side' => $posSide,
                'size' => Money::str($pos->abs()),
                'leverage' => Money::str(Money::of($row->lever)),
                'entry' => Money::str(Money::of($row->avg_px)),
                'mark' => Money::str(Money::of($row->mark_px)),
                'liq_px' => Money::str(Money::of($row->liq_px)),
                'margin' => Money::str(Money::of($row->margin)),
                'settle_ccy' => $this->settleCcy($row),
                'upl_usd' => Money::isUsdStable($this->settleCcy($row)) ? Money::str($upl) : null,
                'upl_pct' => $ratio === null ? null : Money::str($ratio->multipliedBy(100)),
                'margin_mode' => $this->s($row->mgn_mode),
                'opened_at' => $row->c_time === null ? null : $this->iso($this->utc($row->c_time)),
                // Phase 2 (Executor/agents) fills these in.
                'stop' => null,
                'take_profits' => null,
                'agent' => null,
                'proposal_id' => null,
            ];
        }

        return ['taken_at' => $this->iso($this->utc($takenAt)), 'items' => $items];
    }

    /**
     * @return array<string, string|null>
     */
    public function cashflow(object $row): array
    {
        $amount = Money::of($row->amount ?? null);
        $ccy = $this->s($row->ccy ?? null);

        return [
            'id' => $this->s($row->id ?? null),
            'kind' => $this->s($row->kind ?? null),
            'ccy' => $ccy,
            'amount' => Money::str($amount),
            'amount_usd' => Money::isUsdStable($ccy) ? Money::str($amount) : null,
            'state' => $this->s($row->state ?? null),
            'tx_id' => $this->s($row->tx_id ?? null),
            'ts' => isset($row->ts) ? $this->iso($this->utc($row->ts)) : null,
        ];
    }

    public function rangeStart(string $range, CarbonImmutable $now): ?CarbonImmutable
    {
        return match ($range) {
            '1D' => $now->subDay(),
            '1W' => $now->subWeek(),
            '1M' => $now->subMonthNoOverflow(),
            '3M' => $now->subMonthsNoOverflow(3),
            'YTD' => $now->setTimezone(self::OWNER_TIMEZONE)->startOfYear()->utc(),
            default => null,
        };
    }

    /**
     * Successful deposits − withdrawals in USD stablecoins, all-time or strictly after $after.
     */
    private function netDeposits(string $env, ?CarbonImmutable $after): BigDecimal
    {
        $q = DB::table('deposits_withdrawals')
            ->where('env', $env)
            ->where('state', self::CASHFLOW_SUCCESS_STATE)
            ->whereIn(DB::raw('upper(ccy)'), Money::USD_STABLE);
        if ($after !== null) {
            $q->where('ts', '>', $this->db($after));
        }
        $sum = $q->selectRaw("coalesce(sum(CASE WHEN kind = 'deposit' THEN amount ELSE -amount END), 0) AS s")->value('s');

        return Money::of($sum) ?? BigDecimal::zero();
    }

    private function unrealisedPnl(string $env): ?BigDecimal
    {
        $positions = $this->positions($env);
        if ($positions['taken_at'] === null) {
            return null;
        }
        $sum = BigDecimal::zero();
        foreach ($positions['items'] as $p) {
            if ($p['upl_usd'] !== null) {
                $sum = $sum->plus($p['upl_usd']);
            }
        }

        return $sum;
    }

    /** Positive = paid to OKX. */
    private function feesPaid(string $env): BigDecimal
    {
        $sum = DB::table('bills')->where('env', $env)
            ->whereIn(DB::raw('upper(ccy)'), Money::USD_STABLE)
            ->selectRaw('coalesce(sum(fee), 0) AS s')->value('s');

        return (Money::of($sum) ?? BigDecimal::zero())->negated();
    }

    /** Positive = funding received. */
    private function fundingNet(string $env): BigDecimal
    {
        $sum = DB::table('bills')->where('env', $env)
            ->where('type', BillType::FundingFee->value)
            ->whereIn(DB::raw('upper(ccy)'), Money::USD_STABLE)
            ->selectRaw('coalesce(sum(bal_chg), 0) AS s')->value('s');

        return Money::of($sum) ?? BigDecimal::zero();
    }

    private function settleCcy(object $row): ?string
    {
        $settle = $this->s($row->settle_ccy ?? null);
        if ($settle !== null && $settle !== '') {
            return $settle;
        }
        // Fallback when the instrument is not synced yet: "BTC-USDT-SWAP" → "USDT".
        $parts = explode('-', (string) $this->s($row->inst_id ?? null));

        return $parts[1] ?? null;
    }

    private function s(mixed $value): ?string
    {
        return $value === null ? null : (is_scalar($value) ? (string) $value : null);
    }

    private function utc(mixed $value): CarbonImmutable
    {
        return CarbonImmutable::parse(is_string($value) ? $value : (string) $this->s($value))->utc();
    }

    /** Timestamp literal with offset, so timestamptz comparisons never depend on the session zone. */
    private function db(CarbonImmutable $ts): string
    {
        return $ts->utc()->format('Y-m-d H:i:s.uP');
    }

    private function iso(Carbon|CarbonImmutable $ts): string
    {
        return $ts->utc()->toIso8601ZuluString('microsecond');
    }
}
