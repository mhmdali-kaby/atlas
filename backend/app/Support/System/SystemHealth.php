<?php

declare(strict_types=1);

namespace App\Support\System;

use App\Models\AlgoOrder;
use App\Models\Order;
use App\Support\Portfolio\PortfolioService;
use Carbon\CarbonImmutable;
use Illuminate\Redis\RedisManager;
use Illuminate\Support\Facades\DB;
use Throwable;

/**
 * GET /api/system/health (ATL-148). Read-only and never calls trading_core or OKX, so it answers
 * even when trading_core is down:
 *
 * - `trading_state` (ATL-127/145/147): paused, reason, paused_at and the active circuit breakers;
 * - the live market snapshot hashes `atlas:md:<env>:<instId>` (ATL-125) for the always-subscribed
 *   instruments and every instrument with an open position or open order;
 * - the private socket hash `atlas:pws:<env>` (ATL-142);
 * - `sync_status` stream `reconcile` (ATL-146) and `account` (the REST sync).
 */
final class SystemHealth
{
    public function __construct(
        private readonly RedisManager $redis,
        private readonly PortfolioService $portfolio,
    ) {}

    /**
     * @return array<string, mixed>
     */
    public function build(string $env, CarbonImmutable $now): array
    {
        $maxAge = config('atlas_defaults.market_ws.market_data_max_age_seconds');
        $maxAge = is_int($maxAge) ? $maxAge : 30;

        $state = DB::table('trading_state')->where('env', $env)->first();
        $breakers = [];
        $raw = $state === null ? [] : json_decode(is_string($state->breakers) ? $state->breakers : '{}', true);
        foreach (is_array($raw) ? $raw : [] as $name => $entry) {
            if (! is_array($entry) || ($entry['active'] ?? false) !== true) {
                continue;
            }
            $breakers[] = [
                'name' => (string) $name,
                'reason' => is_string($entry['reason'] ?? null) ? $entry['reason'] : null,
                'since' => is_string($entry['since'] ?? null) ? $entry['since'] : null,
                'self_clearing' => ($entry['self_clearing'] ?? false) === true,
                'detail' => is_array($entry['detail'] ?? null) ? $entry['detail'] : null,
            ];
        }
        usort($breakers, fn (array $a, array $b): int => strcmp($a['name'], $b['name']));

        $market = $this->market($env, $now, $maxAge);
        $private = $this->privateFeed($env, $now, $maxAge);

        return [
            'env' => $env,
            'as_of' => self::iso($now),
            'trading' => [
                'paused' => $state !== null && (bool) $state->paused,
                'reason' => $state !== null && is_string($state->reason) ? $state->reason : null,
                'paused_at' => $state !== null && $state->paused_at !== null ? self::iso(CarbonImmutable::parse((string) $state->paused_at)) : null,
                // New orders are refused while paused or while any breaker is active (Risk Engine check 1).
                'accepting_new_orders' => ! ($state !== null && (bool) $state->paused) && $breakers === [],
            ],
            'breakers' => $breakers,
            'market_data' => $market,
            'private_ws' => $private,
            'reconciliation' => $this->syncRow($env, 'reconcile'),
            'account_sync' => $this->syncRow($env, 'account'),
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private function market(string $env, CarbonImmutable $now, int $maxAge): array
    {
        $always = config('atlas_defaults.market_ws.always_subscribe');
        $instIds = is_array($always) ? array_values(array_filter($always, 'is_string')) : [];
        foreach ($this->portfolio->positions($env)['items'] as $item) {
            if (is_string($item['inst_id'] ?? null) && $item['inst_id'] !== '') {
                $instIds[] = $item['inst_id'];
            }
        }
        $instIds = array_merge(
            $instIds,
            Order::query()->where('env', $env)->whereIn('state', Order::OPEN_STATES)->distinct()->pluck('inst_id')->all(),
            AlgoOrder::query()->where('env', $env)->whereIn('state', AlgoOrder::OPEN_STATES)->distinct()->pluck('inst_id')->all(),
        );
        $instIds = array_values(array_unique(array_filter($instIds, 'is_string')));
        sort($instIds);

        $prefix = config('atlas_defaults.market_ws.key_prefix');
        $prefix = is_string($prefix) ? $prefix : 'atlas:md';
        $rows = [];
        $available = true;
        try {
            $connection = $this->redis->connection('events');
            foreach ($instIds as $instId) {
                $hash = $connection->command('hgetall', ["{$prefix}:{$env}:{$instId}"]);
                $ms = is_array($hash) ? ($hash['last_msg_ms'] ?? null) : null;
                $rows[] = self::freshness($instId, is_numeric($ms) ? (int) $ms : null, $now, $maxAge);
            }
        } catch (Throwable) {
            $available = false;
            $rows = array_map(fn (string $i): array => self::freshness($i, null, $now, $maxAge), $instIds);
        }

        return [
            'max_age_seconds' => $maxAge,
            'redis_available' => $available,
            'stale' => array_values(array_map(fn (array $r): string => $r['inst_id'], array_filter($rows, fn (array $r): bool => $r['stale']))),
            'instruments' => $rows,
        ];
    }

    /**
     * @return array{inst_id: string, last_msg_at: string|null, age_seconds: int|null, stale: bool}
     */
    private static function freshness(string $instId, ?int $ms, CarbonImmutable $now, int $maxAge): array
    {
        $age = $ms === null ? null : max(0, intdiv($now->getTimestampMs() - $ms, 1000));

        return [
            'inst_id' => $instId,
            'last_msg_at' => $ms === null ? null : self::iso(CarbonImmutable::createFromTimestampMsUTC($ms)),
            'age_seconds' => $age,
            'stale' => $ms === null || $now->getTimestampMs() - $ms > $maxAge * 1000,
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private function privateFeed(string $env, CarbonImmutable $now, int $maxAge): array
    {
        $key = config('atlas_defaults.private_ws.key');
        $key = (is_string($key) ? $key : 'atlas:pws').':'.$env;
        $hash = [];
        $available = true;
        try {
            $raw = $this->redis->connection('events')->command('hgetall', [$key]);
            $hash = is_array($raw) ? $raw : [];
        } catch (Throwable) {
            $available = false;
        }
        $last = is_numeric($hash['last_msg_ms'] ?? null) ? (int) $hash['last_msg_ms'] : null;
        $login = is_numeric($hash['login_ms'] ?? null) ? (int) $hash['login_ms'] : null;
        $connected = ($hash['connected'] ?? '0') === '1';

        return [
            'redis_available' => $available,
            'connected' => $connected,
            'last_msg_at' => $last === null ? null : self::iso(CarbonImmutable::createFromTimestampMsUTC($last)),
            'login_at' => $login === null ? null : self::iso(CarbonImmutable::createFromTimestampMsUTC($login)),
            'age_seconds' => $last === null ? null : max(0, intdiv($now->getTimestampMs() - $last, 1000)),
            // Same rule as atlas_shared.market_data.PrivateFeedSnapshot.is_stale.
            'stale' => ! $connected || $last === null || $now->getTimestampMs() - $last > $maxAge * 1000,
        ];
    }

    /**
     * @return array{last_success_at: string|null, reconciled_at: string|null, last_error: string|null}
     */
    private function syncRow(string $env, string $stream): array
    {
        $row = DB::table('sync_status')->where('env', $env)->where('stream', $stream)->first();
        $at = fn (mixed $v): ?string => $v === null ? null : self::iso(CarbonImmutable::parse((string) $v));

        return [
            'last_success_at' => $row === null ? null : $at($row->last_success_at),
            'reconciled_at' => $row === null ? null : $at($row->reconciled_at),
            'last_error' => $row !== null && is_string($row->last_error) ? $row->last_error : null,
        ];
    }

    private static function iso(CarbonImmutable $at): string
    {
        return $at->utc()->format('Y-m-d\TH:i:s\Z');
    }
}
