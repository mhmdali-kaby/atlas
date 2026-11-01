<?php

declare(strict_types=1);

namespace App\Listeners;

use App\Events\NotificationCreated;
use App\Jobs\SendTelegramNotification;
use App\Models\TelegramConnection;

/**
 * ATL-215: queues the Telegram send for a new notification whose telegram switch was on (ATL-214's
 * hook). Runs inside atlas:bridge, so it only queues; Horizon does the HTTP call. Registered by
 * Laravel's listener discovery (app/Listeners).
 */
final class QueueTelegramNotification
{
    public function handle(NotificationCreated $event): void
    {
        $notification = $event->notification;
        if (($notification->channels['telegram'] ?? false) !== true || ! TelegramConnection::query()->exists()) {
            return;
        }
        SendTelegramNotification::dispatch($notification->id);
    }
}
