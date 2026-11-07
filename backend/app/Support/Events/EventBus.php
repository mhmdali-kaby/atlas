<?php

declare(strict_types=1);

namespace App\Support\Events;

use Illuminate\Redis\Connections\Connection;
use Illuminate\Redis\Connections\PhpRedisConnection;
use Illuminate\Redis\RedisManager;
use Illuminate\Support\Carbon;
use Illuminate\Support\Str;

/**
 * Publishes brief §3.2 event envelopes to the Redis Streams bus, in the shape of
 * atlas_shared.events.EventEnvelope. Each stream entry has one field, `envelope`,
 * holding the envelope as JSON. Every XADD trims the stream to about
 * `atlas_defaults.event_bus.stream_maxlen` entries (MAXLEN ~); Postgres is the record.
 *
 * Key prefix (ATL-211): `config('database.redis.events.options.prefix')`, empty in production,
 * is put in front of every stream name (self::streamKey()) before it reaches Redis. Code keeps
 * using the logical `atlas.*` names (self::STREAMS, the `$stream` argument below); only the
 * physical Redis key carries the prefix. Tests\Support\TestRedis sets ATLAS_REDIS_PREFIX per
 * checkout (which that config value reads), mirroring atlas_shared.bus's own ATLAS_REDIS_PREFIX
 * on the Python side, so parallel worktrees sharing the test Redis never touch each other's
 * streams.
 */
final class EventBus
{
    public const FIELD = 'envelope';

    public const STREAMS = [
        'atlas.warroom',
        'atlas.market',
        'atlas.account',
        'atlas.proposals',
        'atlas.risk',
        'atlas.orders',
        'atlas.system',
    ];

    public const ENVS = ['demo', 'live'];

    public function __construct(private readonly RedisManager $redis) {}

    /**
     * @param  string  $env  demo or live; anything else is refused
     * @param  array<string, mixed>  $payload
     * @param  string|null  $id  envelope id to use (a UUID); a fresh one when null. Pass the
     *                           event_id of a row already persisted so consumers dedupe on it.
     * @return array{id: string, type: string, ts: string, env: string, source: string, correlation_id: string|null, payload: array<string, mixed>}
     */
    public function publish(
        string $stream,
        string $type,
        string $env,
        string $source,
        array $payload,
        ?string $correlationId = null,
        ?string $id = null,
    ): array {
        $key = self::streamKey($stream);
        if (! in_array($env, self::ENVS, true)) {
            // atlas_shared.events.EventEnvelope (and the bridge) accept only these.
            throw new \InvalidArgumentException("Unknown env [{$env}].");
        }

        $envelope = [
            'id' => $id ?? (string) Str::uuid(),
            'type' => $type,
            'ts' => Carbon::now('UTC')->format('Y-m-d\TH:i:s.v\Z'),
            'env' => $env,
            'source' => $source,
            'correlation_id' => $correlationId,
            'payload' => $payload,
        ];

        $json = json_encode($envelope, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION);

        $this->xadd($this->connection(), $key, $json);

        return $envelope;
    }

    public function connection(): Connection
    {
        return $this->redis->connection('events');
    }

    public static function maxLength(): int
    {
        $maxlen = config('atlas_defaults.event_bus.stream_maxlen');

        return is_int($maxlen) && $maxlen > 0
            ? $maxlen
            : throw new \UnexpectedValueException('atlas_defaults.event_bus.stream_maxlen must be a positive integer.');
    }

    /**
     * The configured Redis key prefix; empty (plain `atlas.*` keys) when unset. The same value
     * Predis applies to this connection's typed commands (config/database.php's `redis.events.
     * options.prefix`); raw commands (XADD, XGROUP, ...) need it applied by hand (see streamKey()).
     */
    public static function keyPrefix(): string
    {
        $prefix = config('database.redis.events.options.prefix', '');

        return is_string($prefix) ? $prefix : '';
    }

    /** The physical Redis key of one of the seven bus streams (brief §3.2), prefixed under tests. */
    public static function streamKey(string $stream): string
    {
        if (! in_array($stream, self::STREAMS, true)) {
            throw new \InvalidArgumentException("Unknown event stream [{$stream}].");
        }

        return self::keyPrefix().$stream;
    }

    /** @return list<string> every stream's physical key, in self::STREAMS order. */
    public static function streamKeys(): array
    {
        return array_map(self::streamKey(...), self::STREAMS);
    }

    /** The inverse of streamKey(): the logical stream name of a physical stream key. */
    public static function logicalStream(string $key): string
    {
        $prefix = self::keyPrefix();

        return $prefix !== '' && str_starts_with($key, $prefix) ? substr($key, strlen($prefix)) : $key;
    }

    private function xadd(Connection $connection, string $key, string $json): void
    {
        $args = ['XADD', $key, 'MAXLEN', '~', (string) self::maxLength(), '*', self::FIELD, $json];

        if ($connection instanceof PhpRedisConnection) {
            $connection->client()->rawCommand(...$args);

            return;
        }

        $connection->command('executeRaw', [$args]);
    }
}
