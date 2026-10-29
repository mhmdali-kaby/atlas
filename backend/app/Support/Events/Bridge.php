<?php

declare(strict_types=1);

namespace App\Support\Events;

use Illuminate\Support\Facades\Log;

/**
 * The `atlas:bridge` loop (brief §3.2): reads every atlas.* stream through the consumer group
 * `bridge`, hands each entry to BridgeEventHandler, and XACKs it once handled.
 *
 * - On start, and after any transient failure, it first re-processes the group's pending entries
 *   (delivered but never acked, e.g. the bridge was killed mid-event) via XAUTOCLAIM, so nothing is
 *   lost; persistence is keyed on the envelope id, so nothing is doubled either.
 * - A malformed entry is logged (stream, entry id, reason — never its contents) and acked.
 * - Any other failure leaves the entry pending; the loop backs off and retries it.
 * - stop() (SIGTERM/SIGINT) lets the current entry finish, then run() returns.
 */
final class Bridge
{
    public const GROUP = 'bridge';

    private const BATCH = 100;

    private bool $stopping = false;

    private bool $drainNeeded = true;

    public function __construct(
        private readonly StreamReader $reader,
        private readonly BridgeEventHandler $handler,
    ) {}

    /**
     * Groups start at `0` on atlas.warroom (anything published before the first bridge run is still
     * persisted) and at `$` elsewhere (stale account/system broadcasts are worthless).
     */
    public function ensureGroups(): void
    {
        $starts = [];
        foreach (EventBus::STREAMS as $stream) {
            $starts[$stream] = $stream === 'atlas.warroom' ? '0' : '$';
        }
        $this->reader->ensureGroup(self::GROUP, $starts);
    }

    public function run(string $consumer, int $blockMs, int $retryDelayMs = 1000): void
    {
        $this->ensureGroups();
        Log::info('atlas.bridge.started', ['consumer' => $consumer, 'group' => self::GROUP]);

        while (! $this->stopping) {
            try {
                if ($this->drainNeeded) {
                    $this->drainPending($consumer);
                }
                $this->readOnce($consumer, $blockMs);
            } catch (\Throwable $e) {
                if ($this->stopRequested()) {
                    break; // a signal interrupted the blocking read
                }
                Log::error('atlas.bridge.retrying', ['exception' => $e::class]);
                $this->drainNeeded = true;
                usleep($retryDelayMs * 1000);
            }
        }

        Log::info('atlas.bridge.stopped', ['consumer' => $consumer]);
    }

    public function stop(): void
    {
        $this->stopping = true;
    }

    /** Set asynchronously by a signal handler, so read it through a call rather than a narrowed property. */
    private function stopRequested(): bool
    {
        return $this->stopping;
    }

    /**
     * Process every pending entry of the group on every stream (claimed for `$consumer`).
     *
     * @return int entries handled
     */
    public function drainPending(string $consumer): int
    {
        $handled = 0;
        foreach (EventBus::STREAMS as $stream) {
            $cursor = '0-0';
            do {
                $page = $this->reader->claimPending($stream, self::GROUP, $consumer, 0, $cursor, self::BATCH);
                foreach ($page['deleted'] as $id) {
                    // Trimmed from the stream while pending: nothing left to process.
                    $this->reader->ack($stream, self::GROUP, $id);
                }
                foreach ($page['entries'] as $entry) {
                    if ($this->stopping) {
                        return $handled;
                    }
                    $this->process($stream, $entry['id'], $entry['envelope']);
                    $handled++;
                }
                $cursor = $page['cursor'];
            } while ($cursor !== '0-0');
        }
        $this->drainNeeded = false;

        return $handled;
    }

    /**
     * One XREADGROUP of new entries across all streams.
     *
     * @return int entries handled
     */
    public function readOnce(string $consumer, int $blockMs): int
    {
        $entries = $this->reader->readNew(self::GROUP, $consumer, EventBus::STREAMS, self::BATCH, $blockMs);
        $handled = 0;
        foreach ($entries as $entry) {
            // Entries read but not handled stay pending and are drained on the next start.
            if ($this->stopping) {
                break;
            }
            $this->process($entry['stream'], $entry['id'], $entry['envelope']);
            $handled++;
        }

        return $handled;
    }

    private function process(string $stream, string $id, ?string $envelope): void
    {
        try {
            $this->handler->handle($stream, $envelope);
        } catch (MalformedEvent $e) {
            Log::warning('atlas.bridge.malformed', [
                'stream' => $stream,
                'entry_id' => $id,
                'reason' => $e->getMessage(),
            ]);
        }

        $this->reader->ack($stream, self::GROUP, $id);
    }
}
