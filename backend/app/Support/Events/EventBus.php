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
 * holding the envelope as JSON.
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

    public function __construct(private readonly RedisManager $redis) {}

    /**
     * @param  'demo'|'live'  $env
     * @param  array<string, mixed>  $payload
     * @return array{id: string, type: string, ts: string, env: string, source: string, correlation_id: string|null, payload: array<string, mixed>}
     */
    public function publish(
        string $stream,
        string $type,
        string $env,
        string $source,
        array $payload,
        ?string $correlationId = null,
    ): array {
        if (! in_array($stream, self::STREAMS, true)) {
            throw new \InvalidArgumentException("Unknown event stream [{$stream}].");
        }

        $envelope = [
            'id' => (string) Str::uuid(),
            'type' => $type,
            'ts' => Carbon::now('UTC')->format('Y-m-d\TH:i:s.v\Z'),
            'env' => $env,
            'source' => $source,
            'correlation_id' => $correlationId,
            'payload' => $payload,
        ];

        $json = json_encode($envelope, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION);

        $this->xadd($this->connection(), $stream, $json);

        return $envelope;
    }

    public function connection(): Connection
    {
        return $this->redis->connection('events');
    }

    private function xadd(Connection $connection, string $stream, string $json): void
    {
        if ($connection instanceof PhpRedisConnection) {
            $connection->client()->rawCommand('XADD', $stream, '*', self::FIELD, $json);

            return;
        }

        $connection->command('executeRaw', [['XADD', $stream, '*', self::FIELD, $json]]);
    }
}
