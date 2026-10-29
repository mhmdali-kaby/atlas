<?php

declare(strict_types=1);

namespace App\Support\Events;

use Illuminate\Redis\Connections\Connection;
use Illuminate\Redis\Connections\PhpRedisConnection;
use Illuminate\Redis\Connections\PredisConnection;
use Illuminate\Redis\RedisManager;
use Predis\Command\RawCommand;
use Predis\Response\ErrorInterface as ErrorResponseInterface;
use Predis\Response\ServerException;

/**
 * Consumer-group reads on the event bus streams (XGROUP / XREADGROUP / XAUTOCLAIM / XACK),
 * as raw commands on the `events` Redis connection, so the reply shapes are Redis's own.
 *
 * An entry is returned as `{id, envelope}` where `envelope` is the raw `envelope` field
 * (null when the entry has no such field, or was trimmed while pending).
 */
final class StreamReader
{
    public function __construct(private readonly RedisManager $redis) {}

    /**
     * Create the group on each stream unless it exists. `$startIds` maps a stream to the id the
     * group starts from (`0` = everything still in the stream, `$` = only new entries).
     *
     * @param  array<string, string>  $startIds
     */
    public function ensureGroup(string $group, array $startIds): void
    {
        foreach ($startIds as $stream => $start) {
            $this->raw(['XGROUP', 'CREATE', $stream, $group, $start, 'MKSTREAM'], allowError: 'BUSYGROUP');
        }
    }

    /**
     * New entries for this consumer (`>`), across all streams, waiting up to `$blockMs`.
     *
     * @param  list<string>  $streams
     * @return list<array{stream: string, id: string, envelope: string|null}>
     */
    public function readNew(string $group, string $consumer, array $streams, int $count, int $blockMs): array
    {
        $args = ['XREADGROUP', 'GROUP', $group, $consumer, 'COUNT', (string) $count, 'BLOCK', (string) $blockMs, 'STREAMS'];
        foreach ($streams as $stream) {
            $args[] = $stream;
        }
        foreach ($streams as $_) {
            $args[] = '>';
        }

        $reply = $this->raw($args);
        if (! is_array($reply)) {
            return []; // nil: the block timed out
        }

        $out = [];
        foreach ($reply as $streamReply) {
            if (! is_array($streamReply) || ! isset($streamReply[0], $streamReply[1]) || ! is_array($streamReply[1])) {
                continue;
            }
            foreach ($streamReply[1] as $entry) {
                $parsed = self::entry($entry);
                if ($parsed !== null) {
                    $out[] = ['stream' => (string) $streamReply[0]] + $parsed;
                }
            }
        }

        return $out;
    }

    /**
     * Claim one page of pending entries (from any consumer of the group) idle for at least
     * `$minIdleMs`, starting at `$cursor`.
     *
     * @return array{cursor: string, entries: list<array{id: string, envelope: string|null}>, deleted: list<string>}
     */
    public function claimPending(string $stream, string $group, string $consumer, int $minIdleMs, string $cursor, int $count): array
    {
        $reply = $this->raw(['XAUTOCLAIM', $stream, $group, $consumer, (string) $minIdleMs, $cursor, 'COUNT', (string) $count]);
        if (! is_array($reply) || ! isset($reply[0])) {
            return ['cursor' => '0-0', 'entries' => [], 'deleted' => []];
        }

        $entries = [];
        $deleted = [];
        foreach (is_array($reply[1] ?? null) ? $reply[1] : [] as $entry) {
            // Redis < 7 reports an entry trimmed while pending as [id, nil].
            if (is_array($entry) && isset($entry[0]) && ! is_array($entry[1] ?? null)) {
                $deleted[] = (string) $entry[0];

                continue;
            }
            $parsed = self::entry($entry);
            if ($parsed !== null) {
                $entries[] = $parsed;
            }
        }
        foreach (is_array($reply[2] ?? null) ? $reply[2] : [] as $id) {
            $deleted[] = (string) $id;
        }

        return ['cursor' => (string) $reply[0], 'entries' => $entries, 'deleted' => $deleted];
    }

    public function ack(string $stream, string $group, string $id): void
    {
        $this->raw(['XACK', $stream, $group, $id]);
    }

    /**
     * Entry ids pending (delivered, not acked) in the group on a stream.
     *
     * @return list<string>
     */
    public function pendingIds(string $stream, string $group, int $count = 1000): array
    {
        $reply = $this->raw(['XPENDING', $stream, $group, '-', '+', (string) $count]);

        $ids = [];
        foreach (is_array($reply) ? $reply : [] as $row) {
            if (is_array($row) && isset($row[0])) {
                $ids[] = (string) $row[0];
            }
        }

        return $ids;
    }

    /**
     * @param  list<string>  $args
     */
    public function raw(array $args, ?string $allowError = null): mixed
    {
        $connection = $this->connection();

        if ($connection instanceof PhpRedisConnection) {
            $client = $connection->client();
            $reply = $client->rawCommand(...$args);
            $error = $client->getLastError();
            $client->clearLastError();
            if ($reply === false && is_string($error)) {
                return $this->failed($args[0], $error, $allowError);
            }

            return $reply;
        }

        if ($connection instanceof PredisConnection) {
            try {
                $reply = $connection->client()->executeCommand(new RawCommand($args[0], array_slice($args, 1)));
            } catch (ServerException $e) {
                return $this->failed($args[0], $e->getMessage(), $allowError);
            }
            if ($reply instanceof ErrorResponseInterface) {
                return $this->failed($args[0], $reply->getMessage(), $allowError);
            }

            return $reply;
        }

        return $connection->command('executeRaw', [$args]);
    }

    private function failed(string $command, string $error, ?string $allowError): null
    {
        if ($allowError !== null && str_starts_with($error, $allowError)) {
            return null;
        }

        throw new \RuntimeException("Redis {$command} failed: {$error}");
    }

    private function connection(): Connection
    {
        return $this->redis->connection('events');
    }

    /**
     * @return array{id: string, envelope: string|null}|null
     */
    private static function entry(mixed $entry): ?array
    {
        if (! is_array($entry) || ! isset($entry[0])) {
            return null;
        }

        $envelope = null;
        $fields = is_array($entry[1] ?? null) ? array_values($entry[1]) : [];
        for ($i = 0; $i + 1 < count($fields); $i += 2) {
            if ($fields[$i] === EventBus::FIELD && is_string($fields[$i + 1])) {
                $envelope = $fields[$i + 1];
            }
        }

        return ['id' => (string) $entry[0], 'envelope' => $envelope];
    }
}
