<?php

declare(strict_types=1);

namespace App\Support\Proposals;

use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Redis;
use Throwable;

/**
 * The live mark price of one instrument, read from trading_core's market snapshot hash
 * `<market_ws.key_prefix>:<env>:<instId>` (ATL-125, atlas_shared.market_data): `mark` is the JSON
 * of the last OKX mark-price push and `mark-price_ms` the Mac's clock when trading_core received
 * it. `mark_at` is that receive time, not OKX's ts, so the browser's 30 s freshness rule matches
 * the Risk Engine's (ATL-188). Missing hash, field, unparsable price or Redis down → both null.
 * Nothing here decides freshness: the card does, on its own clock tick.
 */
final class MarkPrice
{
    public const FIELD = 'mark';

    public const RECEIVED_MS = 'mark-price_ms';

    /**
     * @return array{mark: string|null, mark_at: string|null}
     */
    public static function of(string $env, string $instId): array
    {
        $none = ['mark' => null, 'mark_at' => null];
        $prefix = config('atlas_defaults.market_ws.key_prefix');
        $prefix = is_string($prefix) ? $prefix : 'atlas:md';
        try {
            $raw = Redis::connection('events')->command('hmget', ["{$prefix}:{$env}:{$instId}", self::FIELD, self::RECEIVED_MS]);
        } catch (Throwable) {
            return $none;
        }
        if (! is_array($raw) || ! is_string($raw[0] ?? null) || ! is_numeric($raw[1] ?? null)) {
            return $none;
        }
        $push = json_decode($raw[0], true);
        $px = is_array($push) ? ($push['markPx'] ?? null) : null;
        if (! is_string($px) || preg_match('/^\d+(\.\d+)?$/', $px) !== 1 || ($push['instId'] ?? $instId) !== $instId) {
            return $none;
        }

        return [
            'mark' => $px,
            'mark_at' => Carbon::createFromTimestampMsUTC((int) $raw[1])->format('Y-m-d\TH:i:s.v\Z'),
        ];
    }
}
