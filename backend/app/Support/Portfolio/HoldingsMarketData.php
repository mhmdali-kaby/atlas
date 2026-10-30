<?php

declare(strict_types=1);

namespace App\Support\Portfolio;

use Brick\Math\BigDecimal;
use Brick\Math\RoundingMode;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;

/**
 * Average cost and 24 h change for one holdings currency (ATL-176, brief §13 Holdings).
 *
 * avg_cost_usd is the moving average cost of the asset's spot fills against a USD quote
 * (CCY-USDT, CCY-USDC): a buy adds price × size to the cost, a sell removes size at the current
 * average (so the average does not change), and a position sold down to zero starts again.
 * Fees are not included. Fixed decimals (not stripped). Null when there are no such fills or nothing is left from them.
 *
 * change_24h_pct compares the newest candle close of the asset's price instrument (spot first,
 * then the swap, per quote in portfolio_holdings.quotes) with the newest close at least 24 h
 * before it. Null with a reason: no_instrument (none of those instIds is in the env's universe)
 * or no_candles (they are, but no two closes 24 h apart are stored).
 */
final class HoldingsMarketData
{
    public const REASON_NO_INSTRUMENT = 'no_instrument';

    public const REASON_NO_CANDLES = 'no_candles';

    /**
     * @return array{avg_cost_usd: string|null, change_24h_pct: string|null, change_24h_reason: string|null, change_24h_inst_id: string|null}
     */
    public function forCurrency(string $env, string $ccy): array
    {
        $change = $this->change24h($env, $ccy);

        return [
            'avg_cost_usd' => $this->avgCost($env, $ccy)?->toString(),
            'change_24h_pct' => Money::str($change['pct']),
            'change_24h_reason' => $change['reason'],
            'change_24h_inst_id' => $change['inst_id'],
        ];
    }

    public function avgCost(string $env, string $ccy): ?BigDecimal
    {
        $spotIds = array_map(fn (string $q): string => strtoupper($ccy).'-'.$q, $this->quotes());

        $fills = DB::table('fills')
            ->where('env', $env)
            ->whereIn('inst_id', $spotIds)
            ->orderBy('ts')
            ->orderBy('id')
            ->get(['inst_id', 'side', 'fill_px', 'fill_sz']);
        if ($fills->isEmpty()) {
            return null;
        }

        $qty = BigDecimal::zero();
        $cost = BigDecimal::zero();
        foreach ($fills as $fill) {
            $px = Money::of($fill->fill_px) ?? BigDecimal::zero();
            $sz = Money::of($fill->fill_sz) ?? BigDecimal::zero();
            if ($fill->side === 'buy') {
                $qty = $qty->plus($sz);
                $cost = $cost->plus($px->multipliedBy($sz));

                continue;
            }
            if ($qty->isZero()) {
                continue;
            }
            if ($sz->isGreaterThanOrEqualTo($qty)) {
                $qty = BigDecimal::zero();
                $cost = BigDecimal::zero();

                continue;
            }
            // Remove the sold size at the current average: cost × (qty − sz) / qty.
            $cost = $cost->multipliedBy($qty->minus($sz))->dividedBy($qty, 36, RoundingMode::HalfUp);
            $qty = $qty->minus($sz);
        }

        if ($qty->isNegativeOrZero()) {
            return null;
        }

        $avg = $cost->dividedBy($qty, 36, RoundingMode::HalfUp);

        return $avg->toScale($this->avgCostScale($env, $spotIds, $avg), RoundingMode::HalfUp);
    }

    /**
     * @return array{pct: BigDecimal|null, reason: string|null, inst_id: string|null}
     */
    public function change24h(string $env, string $ccy): array
    {
        $candidates = [];
        foreach ($this->quotes() as $quote) {
            $candidates[] = strtoupper($ccy).'-'.$quote;
            $candidates[] = strtoupper($ccy).'-'.$quote.'-SWAP';
        }
        $known = DB::table('instruments')->where('env', $env)->whereIn('inst_id', $candidates)->pluck('inst_id')->all();
        $instIds = array_values(array_filter($candidates, fn (string $id): bool => in_array($id, $known, true)));
        if ($instIds === []) {
            return ['pct' => null, 'reason' => self::REASON_NO_INSTRUMENT, 'inst_id' => null];
        }

        $hours = $this->int('portfolio_holdings.change_window_hours', 24);
        foreach ($instIds as $instId) {
            foreach ($this->strings('portfolio_holdings.change_bars', ['1H', '1D']) as $bar) {
                $latest = DB::table('candles')->where('env', $env)->where('inst_id', $instId)->where('bar', $bar)
                    ->orderByDesc('ts')->first(['ts', 'c']);
                if ($latest === null) {
                    continue;
                }
                $cutoff = CarbonImmutable::parse((string) $latest->ts)->utc()->subHours($hours);
                $before = DB::table('candles')->where('env', $env)->where('inst_id', $instId)->where('bar', $bar)
                    ->where('ts', '<=', $cutoff->format('Y-m-d H:i:s.uP'))
                    ->orderByDesc('ts')->first(['c']);
                $now = Money::of($latest->c);
                $then = Money::of($before?->c);
                if ($now === null || $then === null || ! $then->isPositive()) {
                    continue;
                }

                return [
                    'pct' => Money::pct($now->minus($then), $then),
                    'reason' => null,
                    'inst_id' => $instId,
                ];
            }
        }

        return ['pct' => null, 'reason' => self::REASON_NO_CANDLES, 'inst_id' => $instIds[0]];
    }

    /**
     * @param  list<string>  $spotIds
     * @return int<0, max>
     */
    private function avgCostScale(string $env, array $spotIds, BigDecimal $avg): int
    {
        $min = $this->int('portfolio_holdings.avg_cost_min_scale', 2);
        $ticks = DB::table('instruments')->where('env', $env)->whereIn('inst_id', $spotIds)->pluck('tick_sz')->all();
        if ($ticks === []) {
            return $avg->isLessThan(1) ? max($min, $this->int('portfolio_holdings.avg_cost_small_scale', 8)) : $min;
        }
        $scale = $min;
        foreach ($ticks as $tick) {
            $t = Money::of($tick);
            if ($t !== null) {
                $scale = max($scale, $t->strippedOfTrailingZeros()->getScale());
            }
        }

        return $scale;
    }

    /** @return list<string> */
    private function quotes(): array
    {
        return array_map('strtoupper', $this->strings('portfolio_holdings.quotes', ['USDT', 'USDC']));
    }

    /**
     * @param  list<string>  $default
     * @return list<string>
     */
    private function strings(string $key, array $default): array
    {
        $value = config('atlas_defaults.'.$key);
        if (! is_array($value)) {
            return $default;
        }

        return array_values(array_filter($value, 'is_string'));
    }

    /**
     * @param  int<0, max>  $default
     * @return int<0, max>
     */
    private function int(string $key, int $default): int
    {
        $value = config('atlas_defaults.'.$key);

        return is_numeric($value) ? max(0, (int) $value) : $default;
    }
}
