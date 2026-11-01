<?php

declare(strict_types=1);

namespace App\Support\Notifications;

use App\Events\NotificationCreated;
use App\Http\Resources\NotificationResource;
use App\Models\Notification;
use App\Models\Setting;
use App\Models\WarroomMessage;
use App\Support\Settings\SettingsSchema;
use App\Support\Settings\SettingsService;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Broadcast;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Str;

/**
 * Turns a bus envelope into a stored, broadcast notification (brief §17, decision #254 (2),
 * ATL-214). Called by BridgeEventHandler for every entry.
 *
 * 1. NotificationMapper says whether the envelope is a §17 event; most are not.
 * 2. The env's settings `notifications.<event>.<channel>` decide: every channel off → nothing is
 *    stored. Otherwise one row is inserted, keyed on the envelope id (ON CONFLICT DO NOTHING), so
 *    a replayed or re-drained envelope never stores a second row and is never re-announced.
 * 3. A new row is broadcast as `notification.created` on private-system when in_app or browser is
 *    on, and NotificationCreated is dispatched (the Telegram sender's hook, ATL-215).
 */
final class Notifier
{
    public const BROADCAST_EVENT = 'notification.created';

    public const CHANNEL = 'system';

    public function __construct(
        private readonly NotificationMapper $mapper,
        private readonly SettingsService $settings,
    ) {}

    /**
     * @param  array{id: string, type: string, ts: string, env: 'demo'|'live', source: string, correlation_id: string|null, payload: array<string, mixed>}  $envelope
     */
    public function handle(string $stream, array $envelope, ?WarroomMessage $message = null): ?Notification
    {
        $draft = $this->mapper->map($stream, $envelope, $message);
        if ($draft === null) {
            return null;
        }

        $channels = $this->channels($envelope['env'], $draft['event']);
        if (! in_array(true, $channels, true)) {
            return null;
        }

        $notification = $this->store($envelope, $draft, $channels);
        if ($notification === null) {
            return null; // already stored for this envelope
        }

        if ($channels['in_app'] || $channels['browser']) {
            $this->broadcast($notification);
        }
        NotificationCreated::dispatch($notification);

        return $notification;
    }

    /**
     * @return array{in_app: bool, browser: bool, telegram: bool}
     */
    private function channels(string $env, string $event): array
    {
        $row = Setting::query()->where('env', $env)->first();
        $values = $row !== null ? $this->settings->values($row) : SettingsSchema::defaults();

        $out = ['in_app' => false, 'browser' => false, 'telegram' => false];
        foreach (array_keys($out) as $channel) {
            $out[$channel] = ($values[SettingsSchema::notificationField($event, $channel)] ?? false) === true;
        }

        return $out;
    }

    /**
     * @param  array{id: string, type: string, ts: string, env: 'demo'|'live', source: string, correlation_id: string|null, payload: array<string, mixed>}  $envelope
     * @param  array{event: string, severity: 'info'|'warning'|'critical', params: array<string, mixed>, link: string|null}  $draft
     * @param  array{in_app: bool, browser: bool, telegram: bool}  $channels
     */
    private function store(array $envelope, array $draft, array $channels): ?Notification
    {
        $id = (string) Str::uuid();
        $inserted = DB::table('notifications')->insertOrIgnore([
            'id' => $id,
            'env' => $envelope['env'],
            'event' => $draft['event'],
            'severity' => $draft['severity'],
            'title_key' => NotificationMapper::titleKey($draft['event']),
            'params' => json_encode((object) $draft['params'], JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE),
            'link' => $draft['link'],
            'source_event_id' => strtolower($envelope['id']),
            'channels' => json_encode($channels, JSON_THROW_ON_ERROR),
            // When the event happened, so a drained backlog keeps its order.
            'created_at' => Carbon::parse($envelope['ts'])->utc()->format('Y-m-d H:i:s.uP'),
        ]);

        return $inserted === 1 ? Notification::query()->findOrFail($id) : null;
    }

    private function broadcast(Notification $notification): void
    {
        try {
            Broadcast::private(self::CHANNEL)
                ->as(self::BROADCAST_EVENT)
                ->with((new NotificationResource($notification))->resolve())
                ->sendNow();
        } catch (\Throwable $e) {
            // Postgres is the record and the SPA refetches on reconnect.
            Log::warning('atlas.notifications.broadcast_failed', ['exception' => $e::class]);
        }
    }
}
