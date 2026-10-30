<?php

declare(strict_types=1);

namespace App\Support\Portfolio;

use Brick\Math\BigDecimal;
use Brick\Math\RoundingMode;
use Carbon\CarbonImmutable;
use Illuminate\Database\Query\Builder;
use Illuminate\Database\Query\JoinClause;
use Illuminate\Support\Facades\DB;

/**
 * Portfolio History (brief §13, ATL-172): fills and closed positions, filtered, newest first.
 * Reads Postgres only (trading_core's history sync fills positions_closed and fills).
 *
 * The same query and the same row shape serve the JSON list and the CSV export. Money and sizes are
 * plain decimal strings (no float, no trailing zeros); timestamps are ISO 8601 UTC with
 * microseconds. R is derived here from the linked proposal's stop and is null without one.
 */
final class HistoryQuery
{
    public const FILLS = 'fills';

    public const CLOSED = 'closed';

    public const TYPES = [self::FILLS, self::CLOSED];

    /** Row keys, in CSV column order. */
    public const FILL_COLUMNS = [
        'id', 'ts', 'inst_id', 'asset_class', 'side', 'pos_side', 'price', 'size', 'fee', 'fee_ccy',
        'fill_pnl', 'exec_type', 'trade_id', 'ord_id', 'order_id', 'proposal_id', 'agent', 'origin',
    ];

    public const CLOSED_COLUMNS = [
        'id', 'pos_id', 'inst_id', 'asset_class', 'side', 'pos_side', 'margin_mode', 'close_type',
        'leverage', 'ccy', 'size', 'entry', 'exit', 'realised_pnl', 'pnl', 'fees', 'funding',
        'liq_penalty', 'r', 'outcome', 'opened_at', 'closed_at', 'proposal_id', 'agent', 'setup_tag',
        'origin',
    ];

    /** Closed positions accept the opening order side as well as the direction. */
    private const CLOSED_SIDE = ['buy' => 'long', 'sell' => 'short', 'long' => 'long', 'short' => 'short'];

    public static function timeColumn(string $type): string
    {
        return $type === self::CLOSED ? 'closed_at' : 'ts';
    }

    /**
     * @return list<string>
     */
    public static function columns(string $type): array
    {
        return $type === self::CLOSED ? self::CLOSED_COLUMNS : self::FILL_COLUMNS;
    }

    /**
     * Filtered rows ordered newest first by (time, id), ready for cursorPaginate() or cursor().
     * The joins live in a subquery so the cursor's two keys are plain, unambiguous columns.
     */
    public function build(HistoryFilters $f): Builder
    {
        $inner = $f->type === self::CLOSED ? $this->closed($f) : $this->fills($f);
        $time = self::timeColumn($f->type);

        return DB::query()->fromSub($inner, 'h')->orderByDesc($time)->orderByDesc('id');
    }

    private function fills(HistoryFilters $f): Builder
    {
        $q = DB::table('fills as f')
            ->leftJoin('orders as o', 'o.id', '=', 'f.order_id')
            ->leftJoin('proposals as p', 'p.id', '=', 'o.proposal_id')
            ->leftJoin('instruments as i', static function (JoinClause $j): void {
                $j->on('i.inst_id', '=', 'f.inst_id')->on('i.env', '=', 'f.env');
            })
            ->where('f.env', $f->env)
            ->select([
                'f.id', 'f.ts', 'f.inst_id', 'i.asset_class', 'f.side', 'f.pos_side', 'f.fill_px',
                'f.fill_sz', 'f.fee', 'f.fee_ccy', 'f.fill_pnl', 'f.exec_type', 'f.trade_id', 'f.ord_id',
                'f.order_id', 'o.proposal_id', 'p.author as agent',
            ]);

        $this->common($q, $f, 'f.inst_id', 'f.ts', 'p.author');
        if ($f->side !== null) {
            $q->where('f.side', $f->side);
        }
        $this->winLoss($q, $f, 'f.fill_pnl');

        return $q;
    }

    private function closed(HistoryFilters $f): Builder
    {
        $q = DB::table('positions_closed as c')
            ->leftJoin('proposals as p', 'p.id', '=', 'c.proposal_id')
            ->leftJoin('instruments as i', static function (JoinClause $j): void {
                $j->on('i.inst_id', '=', 'c.inst_id')->on('i.env', '=', 'c.env');
            })
            ->where('c.env', $f->env)
            ->select([
                'c.id', 'c.closed_at', 'c.opened_at', 'c.pos_id', 'c.inst_id', 'i.asset_class', 'c.side',
                'c.pos_side', 'c.mgn_mode', 'c.close_type', 'c.lever', 'c.ccy', 'c.size', 'c.entry_px',
                'c.exit_px', 'c.realised_pnl', 'c.pnl', 'c.fee', 'c.funding_fee', 'c.liq_penalty',
                'c.proposal_id', 'c.agent', 'c.setup_tag', 'c.origin',
                'p.stop as proposal_stop', 'p.entry as proposal_entry',
            ]);

        $this->common($q, $f, 'c.inst_id', 'c.closed_at', 'c.agent');
        if ($f->side !== null) {
            $q->where('c.side', self::CLOSED_SIDE[$f->side] ?? $f->side);
        }
        if ($f->strategyTag !== null) {
            $q->where('c.setup_tag', $f->strategyTag);
        }
        $this->winLoss($q, $f, 'c.realised_pnl');

        return $q;
    }

    private function common(Builder $q, HistoryFilters $f, string $inst, string $time, string $agent): void
    {
        if ($f->instId !== null) {
            $q->where($inst, $f->instId);
        }
        if ($f->assetClass !== null) {
            $q->where('i.asset_class', $f->assetClass);
        }
        if ($f->agent !== null) {
            $q->where($agent, $f->agent);
        }
        if ($f->fromUtc !== null) {
            $q->where($time, '>=', self::db($f->fromUtc));
        }
        if ($f->toUtc !== null) {
            $q->where($time, '<', self::db($f->toUtc));
        }
    }

    private function winLoss(Builder $q, HistoryFilters $f, string $pnl): void
    {
        if ($f->winLoss === 'win') {
            $q->where($pnl, '>', 0);
        } elseif ($f->winLoss === 'loss') {
            $q->where($pnl, '<', 0);
        }
    }

    /**
     * One API/CSV row.
     *
     * @return array<string, string|null>
     */
    public function row(string $type, object $row): array
    {
        $r = (array) $row;

        if ($type === self::FILLS) {
            return [
                'id' => self::s($r['id'] ?? null),
                'ts' => self::iso($r['ts'] ?? null),
                'inst_id' => self::s($r['inst_id'] ?? null),
                'asset_class' => self::s($r['asset_class'] ?? null),
                'side' => self::s($r['side'] ?? null),
                'pos_side' => self::s($r['pos_side'] ?? null),
                'price' => Money::str(Money::of($r['fill_px'] ?? null)),
                'size' => Money::str(Money::of($r['fill_sz'] ?? null)),
                'fee' => Money::str(Money::of($r['fee'] ?? null)),
                'fee_ccy' => self::s($r['fee_ccy'] ?? null),
                'fill_pnl' => Money::str(Money::of($r['fill_pnl'] ?? null)),
                'exec_type' => self::s($r['exec_type'] ?? null),
                'trade_id' => self::s($r['trade_id'] ?? null),
                'ord_id' => self::s($r['ord_id'] ?? null),
                'order_id' => self::s($r['order_id'] ?? null),
                'proposal_id' => self::s($r['proposal_id'] ?? null),
                'agent' => self::s($r['agent'] ?? null),
                'origin' => ($r['order_id'] ?? null) !== null ? 'atlas' : 'external',
            ];
        }

        $realised = Money::of($r['realised_pnl'] ?? null);

        return [
            'id' => self::s($r['id'] ?? null),
            'pos_id' => self::s($r['pos_id'] ?? null),
            'inst_id' => self::s($r['inst_id'] ?? null),
            'asset_class' => self::s($r['asset_class'] ?? null),
            'side' => self::s($r['side'] ?? null),
            'pos_side' => self::s($r['pos_side'] ?? null),
            'margin_mode' => self::s($r['mgn_mode'] ?? null),
            'close_type' => self::s($r['close_type'] ?? null),
            'leverage' => Money::str(Money::of($r['lever'] ?? null)),
            'ccy' => self::s($r['ccy'] ?? null),
            'size' => Money::str(Money::of($r['size'] ?? null)),
            'entry' => Money::str(Money::of($r['entry_px'] ?? null)),
            'exit' => Money::str(Money::of($r['exit_px'] ?? null)),
            'realised_pnl' => Money::str($realised),
            'pnl' => Money::str(Money::of($r['pnl'] ?? null)),
            'fees' => Money::str(Money::of($r['fee'] ?? null)),
            'funding' => Money::str(Money::of($r['funding_fee'] ?? null)),
            'liq_penalty' => Money::str(Money::of($r['liq_penalty'] ?? null)),
            'r' => self::rMultiple(
                self::s($r['side'] ?? null),
                Money::of($r['entry_px'] ?? null) ?? Money::of($r['proposal_entry'] ?? null),
                Money::of($r['exit_px'] ?? null),
                Money::of($r['proposal_stop'] ?? null),
            ),
            'outcome' => match (true) {
                $realised === null => null,
                $realised->isPositive() => 'win',
                $realised->isNegative() => 'loss',
                default => 'flat',
            },
            'opened_at' => self::iso($r['opened_at'] ?? null),
            'closed_at' => self::iso($r['closed_at'] ?? null),
            'proposal_id' => self::s($r['proposal_id'] ?? null),
            'agent' => self::s($r['agent'] ?? null),
            'setup_tag' => self::s($r['setup_tag'] ?? null),
            'origin' => self::s($r['origin'] ?? null),
        ];
    }

    /**
     * R = price move in the trade's favour per unit ÷ the initial risk per unit |entry − stop|.
     * Null when there is no proposal stop (external positions), no exit, or a zero stop distance.
     */
    public static function rMultiple(?string $side, ?BigDecimal $entry, ?BigDecimal $exit, ?BigDecimal $stop): ?string
    {
        if ($stop === null || $entry === null || $exit === null || ! in_array($side, ['long', 'short'], true)) {
            return null;
        }
        $risk = $entry->minus($stop)->abs();
        if ($risk->isZero()) {
            return null;
        }
        $move = $side === 'long' ? $exit->minus($entry) : $entry->minus($exit);
        $scale = config('atlas_defaults.portfolio_history.r_scale');
        $scale = is_numeric($scale) ? max(0, (int) $scale) : 2;

        return $move->dividedBy($risk, $scale, RoundingMode::HalfUp)->toString();
    }

    private static function s(mixed $value): ?string
    {
        return $value === null ? null : (is_scalar($value) ? (string) $value : null);
    }

    private static function iso(mixed $value): ?string
    {
        if (! is_string($value) || $value === '') {
            return null;
        }

        return CarbonImmutable::parse($value)->utc()->toIso8601ZuluString('microsecond');
    }

    /** Timestamp literal with offset, so the comparison never depends on the session time zone. */
    private static function db(CarbonImmutable $ts): string
    {
        return $ts->utc()->format('Y-m-d H:i:s.uP');
    }
}
