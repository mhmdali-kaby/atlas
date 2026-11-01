<?php

declare(strict_types=1);

namespace App\Jobs;

use App\Models\Notification;
use App\Models\TelegramConnection;
use App\Support\Telegram\TelegramClient;
use App\Support\Telegram\TelegramException;
use App\Support\Telegram\TelegramMessages;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Queue\Queueable;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Log;

/**
 * Sends one stored notification to the owner's Telegram chat (ATL-215, decision #254 (3)). Queued
 * by the NotificationCreated listener only when the row's channels.telegram is true, so the bridge
 * never waits on Telegram. The job carries the notification id only, never the token or chat id.
 *
 * A Telegram failure is retried (telegram.send_tries) and then stored as the connection's
 * last_error; the exception is a scrubbed TelegramException, so failed_jobs holds no token.
 */
class SendTelegramNotification implements ShouldQueue
{
    use Queueable;

    public int $tries;

    public function __construct(public readonly string $notificationId)
    {
        $tries = config('atlas_defaults.telegram.send_tries');
        $this->tries = is_int($tries) ? $tries : 3;
        $queue = config('atlas_defaults.telegram.queue');
        $this->onQueue(is_string($queue) ? $queue : 'default');
    }

    public function handle(): void
    {
        $notification = Notification::query()->find($this->notificationId);
        $connection = TelegramConnection::current();
        if ($notification === null || $connection === null || ($notification->channels['telegram'] ?? false) !== true) {
            return;
        }

        $locale = TelegramMessages::ownerLocale();
        try {
            TelegramClient::for($connection)->sendMessage(
                $connection->chat_id,
                TelegramMessages::text($notification, $locale),
                TelegramMessages::keyboard($notification, $locale),
            );
        } catch (TelegramException $e) {
            $connection->forceFill(['last_error' => mb_substr($e->getMessage(), 0, 300)])->save();
            Log::warning('atlas.telegram.send_failed', ['notification_id' => $notification->id, 'method' => $e->method, 'http_status' => $e->httpStatus]);

            throw $e;
        }
        $connection->forceFill(['last_sent_at' => Carbon::now(), 'last_error' => null])->save();
    }
}
