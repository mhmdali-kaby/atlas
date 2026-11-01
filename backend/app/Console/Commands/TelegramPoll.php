<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Models\TelegramConnection;
use App\Support\Telegram\TelegramException;
use App\Support\Telegram\TelegramPoller;
use Illuminate\Console\Attributes\Description;
use Illuminate\Console\Attributes\Signature;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\Log;

/**
 * ATL-215, decision #254 (3): reads Telegram button presses with getUpdates long polling — no
 * webhook and nothing exposed (brief §18). Long-running; started by `./atlas up`. With no bot set
 * up it waits and checks again. `--once` runs one round (tests, a manual check).
 */
#[Signature('atlas:telegram-poll {--once : Run a single getUpdates round and exit}')]
#[Description('Read Telegram Approve/Reject button presses by long polling')]
class TelegramPoll extends Command
{
    private bool $stop = false;

    public function handle(TelegramPoller $poller): int
    {
        $timeout = self::int('poll_timeout_seconds', 25);
        $backoff = self::int('error_backoff_seconds', 5);
        $once = (bool) $this->option('once');
        if (! $once && extension_loaded('pcntl')) {
            pcntl_async_signals(true);
            pcntl_signal(SIGTERM, fn () => $this->stop = true);
            pcntl_signal(SIGINT, fn () => $this->stop = true);
        }

        do {
            try {
                $taken = $poller->pollOnce($once ? 0 : $timeout);
                if ($once) {
                    $this->line("Taken {$taken} update(s).");
                }
                if (! $once && $taken === 0 && TelegramConnection::current() === null) {
                    sleep($backoff);
                }
            } catch (TelegramException $e) {
                // Scrubbed message; status and method only in the log.
                Log::warning('atlas.telegram.poll_failed', ['method' => $e->method, 'http_status' => $e->httpStatus, 'error_code' => $e->errorCode]);
                if ($once) {
                    $this->error($e->getMessage());

                    return self::FAILURE;
                }
                sleep($backoff);
            }
        } while (! $once && ! $this->stop);

        return self::SUCCESS;
    }

    private static function int(string $key, int $fallback): int
    {
        $value = config("atlas_defaults.telegram.{$key}");

        return is_int($value) ? $value : $fallback;
    }
}
