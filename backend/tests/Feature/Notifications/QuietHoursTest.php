<?php

declare(strict_types=1);

use App\Jobs\SendTelegramNotification;
use App\Models\Notification;
use App\Models\TelegramConnection;
use App\Models\User;
use App\Support\Events\BridgeEventHandler;
use App\Support\Notifications\QuietHours;
use App\Support\Settings\SettingsService;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Queue;
use Illuminate\Support\Str;
use Tests\Support\RecordingBroadcaster;

/*
 * ATL-229, decision #255 (2): during quiet hours a non-critical notification keeps its in-app row
 * but is not delivered to the browser or Telegram; circuit breaker, kill switch, stop hit, target
 * hit and proposal awaiting approval always are.
 */

/**
 * @param  array<string, mixed>  $payload
 */
function quietEnvelope(string $type, string $source, array $payload): string
{
    return json_encode([
        'id' => (string) Str::uuid(),
        'type' => $type,
        'ts' => '2026-11-02T21:30:00.000000Z',
        'env' => 'demo',
        'source' => $source,
        'correlation_id' => null,
        'payload' => $payload,
    ], JSON_THROW_ON_ERROR);
}

function quietOrderFilled(): string
{
    return quietEnvelope('order.state_changed', 'service:trading_core', [
        'order_id' => (string) Str::uuid(), 'cl_ord_id' => 'atlquiet0000001', 'ord_id' => '2001', 'inst_id' => 'XAU-USDT-SWAP',
        'side' => 'buy', 'pos_side' => 'net', 'ord_type' => 'limit', 'state' => 'filled', 'px' => '2650.1',
        'sz' => '3', 'filled_sz' => '3', 'avg_px' => '2650.1', 'origin' => 'atlas', 'purpose' => 'entry',
        'reduce_only' => false, 'proposal_id' => null, 'risk_check_id' => null,
        'from_state' => 'partially_filled', 'to_state' => 'filled',
    ]);
}

function quietBreaker(): string
{
    return quietEnvelope('system.circuit_breaker', 'service:trading_core.breakers', [
        'name' => 'daily_loss', 'label' => 'Daily loss limit', 'state' => 'tripped', 'reason' => 'Daily loss limit tripped.',
        'detail' => [], 'since' => '2026-11-02T21:30:00Z', 'self_clearing' => false, 'cleared_at' => null, 'cleared_by' => null,
    ]);
}

/** @return list<string> events broadcast as notification.created */
function quietBroadcastEvents(RecordingBroadcaster $broadcasts): array
{
    return array_values(array_map(
        fn (array $b): string => (string) $b['payload']['event'],
        array_filter($broadcasts->sent, fn (array $b): bool => $b['event'] === 'notification.created'),
    ));
}

/**
 * @param  array<string, mixed>  $changes
 */
function quietSettings(array $changes): void
{
    $settings = app(SettingsService::class);
    $settings->update('demo', $settings->current('demo')->version, $changes, User::query()->firstOrFail());
}

beforeEach(function (): void {
    $this->broadcasts = RecordingBroadcaster::install();
    User::factory()->create();
    TelegramConnection::query()->create([
        'bot_token' => '123456:TEST-ONLY-TOKEN-ABCDEFGHIJKLMNOPQRSTUVWXYZ',
        'chat_id' => '987654321',
        'bot_token_last4' => 'WXYZ',
        'chat_id_last4' => '4321',
    ]);
    quietSettings([
        'schedule.quiet_hours_enabled' => true,
        'schedule.quiet_hours_start' => '23:00',
        'schedule.quiet_hours_end' => '07:00',
        'notifications.order_filled.telegram' => true,
        'notifications.circuit_breaker.telegram' => true,
    ]);
    Queue::fake([SendTelegramNotification::class]);
});

afterEach(function (): void {
    Carbon::setTestNow();
});

it('writes an order_filled row with no browser or Telegram delivery during quiet hours, and delivers a circuit_breaker', function (): void {
    Carbon::setTestNow('2026-11-02T21:30:00Z'); // 00:30 Baghdad

    app(BridgeEventHandler::class)->handle('atlas.orders', quietOrderFilled());
    app(BridgeEventHandler::class)->handle('atlas.system', quietBreaker());

    $filled = Notification::query()->where('event', 'order_filled')->sole();
    expect($filled->channels)->toBe(['in_app' => true, 'browser' => false, 'telegram' => false]);
    $breaker = Notification::query()->where('event', 'circuit_breaker')->sole();
    expect($breaker->channels)->toBe(['in_app' => true, 'browser' => true, 'telegram' => true]);

    // The in-app list still gets both; only the breaker can raise a browser banner (channels.browser).
    expect(quietBroadcastEvents($this->broadcasts))->toBe(['order_filled', 'circuit_breaker']);
    Queue::assertPushed(SendTelegramNotification::class, 1);
    Queue::assertPushed(SendTelegramNotification::class, fn (SendTelegramNotification $job): bool => $job->notificationId === $breaker->id);
});

it('delivers order_filled to the browser and Telegram outside quiet hours', function (): void {
    Carbon::setTestNow('2026-11-02T09:00:00Z'); // 12:00 Baghdad

    app(BridgeEventHandler::class)->handle('atlas.orders', quietOrderFilled());

    expect(Notification::query()->where('event', 'order_filled')->sole()->channels)
        ->toBe(['in_app' => true, 'browser' => true, 'telegram' => true]);
    Queue::assertPushed(SendTelegramNotification::class, 1);
});

it('delivers order_filled at night when quiet hours are off', function (): void {
    quietSettings(['schedule.quiet_hours_enabled' => false]);
    Carbon::setTestNow('2026-11-02T21:30:00Z');

    app(BridgeEventHandler::class)->handle('atlas.orders', quietOrderFilled());

    expect(Notification::query()->where('event', 'order_filled')->sole()->channels['browser'])->toBeTrue();
    Queue::assertPushed(SendTelegramNotification::class, 1);
});

it('still writes the in-app row when browser was the only channel switched on', function (): void {
    quietSettings(['notifications.order_filled.in_app' => false, 'notifications.order_filled.telegram' => false]);
    Carbon::setTestNow('2026-11-02T21:30:00Z');

    app(BridgeEventHandler::class)->handle('atlas.orders', quietOrderFilled());

    expect(Notification::query()->where('event', 'order_filled')->sole()->channels)
        ->toBe(['in_app' => false, 'browser' => false, 'telegram' => false])
        ->and(quietBroadcastEvents($this->broadcasts))->toBe([]);
});

it('reads the window in Baghdad time, crossing midnight, end exclusive', function (string $utc, bool $quiet): void {
    $values = [
        'schedule.quiet_hours_enabled' => true,
        'schedule.quiet_hours_start' => '23:00',
        'schedule.quiet_hours_end' => '07:00',
    ];
    expect(QuietHours::active($values, Carbon::parse($utc)))->toBe($quiet);
})->with([
    '22:59 Baghdad' => ['2026-11-02T19:59:00Z', false],
    '23:00 Baghdad' => ['2026-11-02T20:00:00Z', true],
    '02:00 Baghdad' => ['2026-11-02T23:00:00Z', true],
    '06:59 Baghdad' => ['2026-11-03T03:59:00Z', true],
    '07:00 Baghdad' => ['2026-11-03T04:00:00Z', false],
]);

it('lists exactly the events that always deliver', function (): void {
    foreach (['circuit_breaker', 'kill_switch', 'stop_hit', 'target_hit', 'proposal_awaiting_approval'] as $event) {
        expect(QuietHours::alwaysDelivered($event))->toBeTrue();
    }
    foreach (['order_filled', 'daily_report', 'news_alert', 'agent_degraded', 'data_stale', 'reconciliation_mismatch'] as $event) {
        expect(QuietHours::alwaysDelivered($event))->toBeFalse();
    }
});
