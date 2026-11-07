<?php

declare(strict_types=1);

namespace App\Support\Portfolio;

use Brick\Math\BigDecimal;
use Brick\Math\RoundingMode;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;

/**
 * Benchmark series for the equity curve (ATL-176, brief §13 Overview): a symbol's perp candle
 * closes rebased so the series starts at the equity curve's first point in the range.
 *
 * value(t) = base_equity × close(t) / base_close, where base_close is the close of the newest
 * candle at or before the first equity point (the first candle after it when none is older).
 * Reads Postgres only (`instruments`, `candles`, `equity_curve`).
 */
final class BenchmarkService
{
    public const REASON_NOT_IN_UNIVERSE = 'not_in_universe';

    public const REASON_NO_EQUITY = 'no_equity';

    public const REASON_NO_CANDLES = 'no_candles';

    public function __construct(private readonly PortfolioService $portfolio) {}

    /** @return list<string> */
    public static function symbols(): array
    {
        $symbols = config('atlas_defaults.portfolio_benchmark.symbols');

        return is_array($symbols) ? array_map('strval', array_keys($symbols)) : [];
    }

    /**
     * @return array<string, mixed>
     */
    public function series(string $env, string $symbol, string $range): array
    {
        $now = CarbonImmutable::now()->utc();
        $from = $this->portfolio->rangeStart($range, $now);
        $out = [
            'symbol' => $symbol,
            'env' => $env,
            'range' => $range,
            'from' => $from === null ? null : $this->iso($from),
            'to' => $this->iso($now),
            'available' => false,
            'reason' => null,
            'inst_id' => null,
            'bar' => null,
            'base' => null,
            'points' => [],
        ];

        $instId = $this->instrument($env, $symbol);
        if ($instId === null) {
            return ['reason' => self::REASON_NOT_IN_UNIVERSE] + $out;
        }
        $out['inst_id'] = $instId;

        $equity = DB::table('equity_curve')->where('env', $env)->where('ts', '<=', $this->db($now));
        if ($from !== null) {
            $equity->where('ts', '>=', $this->db($from));
        }
        $first = $equity->orderBy('ts')->first(['ts', 'total_eq_usd']);
        $baseEquity = Money::of($first?->total_eq_usd);
        if ($first === null || $baseEquity === null) {
            return ['reason' => self::REASON_NO_EQUITY] + $out;
        }
        $baseTs = CarbonImmutable::parse((string) $first->ts)->utc();

        $scale = $this->int('portfolio_benchmark.value_scale', 8);
        foreach ($this->bars($range) as $bar) {
            $candles = DB::table('candles')->where('env', $env)->where('inst_id', $instId)->where('bar', $bar);
            $baseCandle = (clone $candles)->where('ts', '<=', $this->db($baseTs))->orderByDesc('ts')->first(['ts', 'c'])
                ?? (clone $candles)->where('ts', '>', $this->db($baseTs))->where('ts', '<=', $this->db($now))
                    ->orderBy('ts')->first(['ts', 'c']);
            $baseClose = Money::of($baseCandle?->c);
            if ($baseCandle === null || $baseClose === null || ! $baseClose->isPositive()) {
                continue;
            }

            $points = [['ts' => $this->iso($baseTs), 'close' => (string) Money::str($baseClose), 'value_usd' => (string) Money::str($baseEquity)]];
            $rows = (clone $candles)->where('ts', '>', $this->db($baseTs))->where('ts', '<=', $this->db($now))
                ->orderBy('ts')->get(['ts', 'c']);
            foreach ($rows as $row) {
                $close = Money::of($row->c);
                if ($close === null) {
                    continue;
                }
                $points[] = [
                    'ts' => $this->iso(CarbonImmutable::parse((string) $row->ts)),
                    'close' => (string) Money::str($close),
                    'value_usd' => (string) Money::str($this->rebase($baseEquity, $close, $baseClose, $scale)),
                ];
            }

            return [
                'available' => true,
                'bar' => $bar,
                'base' => [
                    'ts' => $this->iso($baseTs),
                    'equity_usd' => (string) Money::str($baseEquity),
                    'close' => (string) Money::str($baseClose),
                    'candle_ts' => $this->iso(CarbonImmutable::parse((string) $baseCandle->ts)),
                ],
                'points' => $points,
            ] + $out;
        }

        return ['reason' => self::REASON_NO_CANDLES] + $out;
    }

    /**
     * @param  int<0, max>  $scale
     */
    private function rebase(BigDecimal $baseEquity, BigDecimal $close, BigDecimal $baseClose, int $scale): BigDecimal
    {
        return $baseEquity->multipliedBy($close)->dividedBy($baseClose, $scale, RoundingMode::HalfUp);
    }

    private function instrument(string $env, string $symbol): ?string
    {
        $candidates = config('atlas_defaults.portfolio_benchmark.symbols.'.$symbol);
        if (! is_array($candidates)) {
            return null;
        }
        $candidates = array_values(array_filter($candidates, 'is_string'));
        $known = DB::table('instruments')->where('env', $env)->whereIn('inst_id', $candidates)->pluck('inst_id')->all();
        foreach ($candidates as $id) {
            if (in_array($id, $known, true)) {
                return $id;
            }
        }

        return null;
    }

    /** @return list<string> */
    private function bars(string $range): array
    {
        $bars = config('atlas_defaults.portfolio_benchmark.bars.'.$range);

        return is_array($bars) ? array_values(array_filter($bars, 'is_string')) : ['1D'];
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

    private function db(CarbonImmutable $ts): string
    {
        return $ts->utc()->format('Y-m-d H:i:s.uP');
    }

    private function iso(CarbonImmutable $ts): string
    {
        return $ts->utc()->toIso8601ZuluString('microsecond');
    }
}
