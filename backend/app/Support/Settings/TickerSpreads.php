<?php

declare(strict_types=1);

namespace App\Support\Settings;

use Brick\Math\BigDecimal;
use Brick\Math\RoundingMode;
use Carbon\CarbonImmutable;
use Illuminate\Redis\RedisManager;
use Throwable;

/**
 * Bid/ask spread from trading_core's live market snapshot (ATL-125, atlas_shared.market_data):
 * Redis hash `<market_ws.key_prefix>:<env>:<instId>`, field `ticker` (OKX tickers push) and
 * `tickers_ms` (epoch ms of that push). Only fresh tickers count: one older than
 * market_data_max_age_seconds, or one without a positive bid and ask, gives no spread.
 * Read on the `events` connection, which has no Laravel key prefix, like the Python side.
 */
class TickerSpreads
{
    public function __construct(private readonly RedisManager $redis) {}

    /**
     * @param  list<string>  $instIds
     * @return array<string, string> instId => spread in bps (decimal string, 4 dp)
     */
    public function spreadsBps(string $env, array $instIds, CarbonImmutable $now, int $maxAgeSeconds): array
    {
        $prefixConfig = config('atlas_defaults.market_ws.key_prefix');
        $prefix = is_string($prefixConfig) ? $prefixConfig : 'atlas:md';
        $out = [];
        try {
            $connection = $this->redis->connection('events');
            foreach ($instIds as $instId) {
                $raw = $connection->command('hgetall', ["{$prefix}:{$env}:{$instId}"]);
                $spread = is_array($raw) ? self::spread($raw, $now, $maxAgeSeconds) : null;
                if ($spread !== null) {
                    $out[$instId] = $spread;
                }
            }
        } catch (Throwable) {
            // Redis down: spreads are unknown, which the caller reports as spread_unknown.
            return [];
        }

        return $out;
    }

    /**
     * @param  array<mixed>  $hash
     */
    public static function spread(array $hash, CarbonImmutable $now, int $maxAgeSeconds): ?string
    {
        $pushedMs = $hash['tickers_ms'] ?? null;
        if (! is_numeric($pushedMs) || $now->getTimestampMs() - (int) $pushedMs > $maxAgeSeconds * 1000) {
            return null;
        }
        $ticker = is_string($hash['ticker'] ?? null) ? json_decode($hash['ticker'], true) : null;
        if (! is_array($ticker) || ! is_numeric($ticker['bidPx'] ?? null) || ! is_numeric($ticker['askPx'] ?? null)) {
            return null;
        }
        $bid = BigDecimal::of((string) $ticker['bidPx']);
        $ask = BigDecimal::of((string) $ticker['askPx']);
        if (! $bid->isPositive() || $ask->isLessThan($bid)) {
            return null;
        }
        $mid = $bid->plus($ask)->dividedBy(2, 18, RoundingMode::HalfUp);

        return (string) $ask->minus($bid)->multipliedBy(10000)->dividedBy($mid, 4, RoundingMode::HalfUp);
    }
}
