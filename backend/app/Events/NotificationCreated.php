<?php

declare(strict_types=1);

namespace App\Events;

use App\Models\Notification;
use Illuminate\Foundation\Events\Dispatchable;

/**
 * ATL-214 hook point: dispatched once, after a notification row is stored for the first time (never
 * for a replayed envelope). `channels` says which delivery switches were on; the Telegram sender
 * (ATL-215) listens and acts when channels['telegram'] is true. Not broadcast itself: the bridge
 * already broadcasts `notification.created` on private-system when in_app or browser is on.
 */
final class NotificationCreated
{
    use Dispatchable;

    public function __construct(public readonly Notification $notification) {}
}
