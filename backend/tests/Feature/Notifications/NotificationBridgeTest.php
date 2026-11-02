<?php

declare(strict_types=1);

use App\Events\NotificationCreated;
use App\Models\Connection;
use App\Models\Notification;
use App\Models\User;
use App\Support\Events\Bridge;
use App\Support\Events\BridgeEventHandler;
use App\Support\Events\EventBus;
use App\Support\Settings\SettingsSchema;
use App\Support\Settings\SettingsService;
use Illuminate\Support\Facades\Event;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Str;
use Tests\Support\RecordingBroadcaster;

/*
 * ATL-214, brief §17: every §17 event on the bus becomes exactly one stored notification, broadcast
 * as `notification.created` on private-system. The envelopes below are copies of what the Python
 * publishers emit (trading_core recorder/private WS/breakers/kill, atlas_agents approval/session/
 * daily report, atlas_ingest news.alert).
 */

const NOTIF_PROPOSAL = '0b8f1c52-7d0e-4a8e-9a39-3b4c1e0f6a11';
const NOTIF_ORDER = '6a2d4c10-1f7e-4c55-8d61-0e9b2a3f4c22';
const NOTIF_ALGO = '9c3e5d21-2a8f-4d66-9e72-1fa03b405d33';

/**
 * @param  array<string, mixed>  $payload
 * @return array<string, mixed>
 */
function notifEnvelope(string $type, string $source, array $payload, string $env = 'demo'): array
{
    return [
        'id' => (string) Str::uuid(),
        'type' => $type,
        'ts' => '2026-11-02T09:15:00.250000Z',
        'env' => $env,
        'source' => $source,
        'correlation_id' => 'cycle-2026-11-02',
        'payload' => $payload,
    ];
}

/**
 * @return array<string, array{0: string, 1: array<string, mixed>, 2: string, 3: string, 4: 'info'|'warning'|'critical'}>
 */
function notifCases(): array
{
    $order = [
        'order_id' => NOTIF_ORDER, 'cl_ord_id' => 'atl0b8f1c527d0e1', 'ord_id' => '2001', 'inst_id' => 'XAU-USDT-SWAP',
        'side' => 'buy', 'pos_side' => 'net', 'ord_type' => 'limit', 'state' => 'filled', 'px' => '2650.1',
        'sz' => '3', 'filled_sz' => '3', 'avg_px' => '2650.1', 'origin' => 'atlas', 'purpose' => 'entry',
        'reduce_only' => false, 'proposal_id' => NOTIF_PROPOSAL, 'risk_check_id' => null,
        'from_state' => 'partially_filled', 'to_state' => 'filled',
    ];
    $algo = [
        'algo_order_id' => NOTIF_ALGO, 'algo_id' => '3001', 'algo_cl_ord_id' => 'atlsl0b8f1c527d0e', 'inst_id' => 'BTC-USDT-SWAP',
        'ord_type' => 'conditional', 'kind' => 'sl', 'side' => 'sell', 'pos_side' => 'net', 'state' => 'effective',
        'okx_state' => 'effective', 'sl_trigger_px' => '64000', 'tp_trigger_px' => null, 'trigger_px' => null,
        'actual_side' => 'sl', 'actual_px' => '63990.5', 'sz' => '2', 'ord_id' => '4001', 'origin' => 'atlas',
        'late' => false, 'u_time' => '2026-11-02T09:15:00.000Z', 'from_state' => 'live', 'to_state' => 'effective',
    ];
    $breaker = fn (string $name, string $label) => [
        'name' => $name, 'label' => $label, 'state' => 'tripped', 'reason' => "{$label} tripped.",
        'detail' => [], 'since' => '2026-11-02T09:15:00Z', 'self_clearing' => $name === 'data_stale',
        'cleared_at' => null, 'cleared_by' => null,
    ];

    return [
        'proposal_awaiting_approval' => ['atlas.proposals', notifEnvelope('proposal.awaiting_owner', 'agent:orchestrator', [
            'proposal_id' => NOTIF_PROPOSAL, 'version' => 1, 'cycle_id' => null, 'kind' => 'entry',
            'inst_id' => 'XAU-USDT-SWAP', 'side' => 'buy', 'risk_check_id' => (string) Str::uuid(),
            'result' => 'approved', 'decision' => 'awaiting_owner', 'final_contracts' => '3',
            'final_notional_usd' => '7950.30', 'leverage' => 3, 'approval_expires_at' => '2026-11-02T09:30:00Z',
        ]), 'proposal_awaiting_approval', '/warroom?proposal='.NOTIF_PROPOSAL, 'warning'],
        'order_filled' => ['atlas.orders', notifEnvelope('order.state_changed', 'service:trading_core', $order),
            'order_filled', '/portfolio/orders?inst_id=XAU-USDT-SWAP', 'info'],
        'stop_hit' => ['atlas.orders', notifEnvelope('algo_order.state_changed', 'service:trading_core.ws', $algo),
            'stop_hit', '/portfolio/positions?inst_id=BTC-USDT-SWAP', 'warning'],
        'target_hit' => ['atlas.orders', notifEnvelope('algo_order.state_changed', 'service:trading_core.ws', [
            ...$algo, 'ord_type' => 'oco', 'kind' => 'oco', 'sl_trigger_px' => '64000', 'tp_trigger_px' => '70000',
            'actual_side' => 'tp', 'actual_px' => '70010',
        ]), 'target_hit', '/portfolio/positions?inst_id=BTC-USDT-SWAP', 'info'],
        'circuit_breaker' => ['atlas.system', notifEnvelope('system.circuit_breaker', 'service:trading_core.breakers', $breaker('daily_loss', 'Daily loss limit')),
            'circuit_breaker', '/warroom', 'critical'],
        'kill_switch' => ['atlas.system', notifEnvelope('system.kill', 'service:trading_core', [
            'ok' => true, 'env' => 'demo', 'flatten' => true, 'at' => '2026-11-02T09:15:00Z', 'cancelled' => 4,
            'cancelled_orders' => 3, 'cancelled_algos' => 1, 'already_done' => 0, 'closed' => 2, 'stops_kept' => 2,
            'failed' => [], 'items' => [], 'paused' => true,
        ]), 'kill_switch', '/warroom', 'critical'],
        'daily_report' => ['atlas.warroom', notifEnvelope('warroom.message', 'agent:portfolio_manager', [
            'thread' => ['kind' => 'main', 'key' => 'main'], 'author_type' => 'agent', 'author' => 'portfolio_manager',
            'kind' => 'research_report', 'content_md' => '**Daily report** 2026-11-02: equity +0.4%.',
            'card' => ['type' => 'daily_report', 'day' => '2026-11-02', 'env' => 'demo'],
            'reply_to' => null, 'mentions' => [], 'reasoning_md' => null, 'tool_calls' => null,
            'tokens_in' => null, 'tokens_out' => null, 'cost_usd' => null,
        ]), 'daily_report', '/warroom?message=', 'info'],
        'news_alert' => ['atlas.warroom', notifEnvelope('news.alert', 'system:news', [
            'news_item_id' => (string) Str::uuid(), 'cluster_id' => (string) Str::uuid(),
            'headline' => 'Gold jumps after surprise CPI print', 'source' => 'reuters', 'source_name' => 'Reuters',
            'category' => 'markets_news', 'url' => 'https://www.reuters.com/markets/gold-cpi', 'published_at' => '2026-11-02T09:14:00Z',
            'importance' => 4, 'event_type' => 'macro_release', 'inst_ids' => ['XAU-USDT-SWAP'], 'asset_classes' => ['commodity'],
            'sentiment' => 0.6, 'novelty' => 0.8, 'reason' => 'held_instrument', 'held_inst_ids' => ['XAU-USDT-SWAP'],
        ]), 'news_alert', '/warroom?message=', 'warning'],
        'agent_degraded' => ['atlas.system', notifEnvelope('system.agent_state', 'agent:historian', [
            'agent' => 'historian', 'state' => 'rate_limited', 'previous_state' => 'thinking', 'reason' => 'usage limit',
            'run_id' => (string) Str::uuid(), 'current_task' => null, 'last_error' => 'rate_limited: Claude usage limit',
            'resets_at' => '2026-11-02T12:00:00+00:00',
        ]), 'agent_degraded', '/agents', 'warning'],
        'data_stale' => ['atlas.system', notifEnvelope('system.circuit_breaker', 'service:trading_core.breakers', $breaker('data_stale', 'Stale market data')),
            'data_stale', '/settings/data', 'warning'],
        'reconciliation_mismatch' => ['atlas.system', notifEnvelope('system.circuit_breaker', 'service:trading_core.breakers', $breaker('reconciliation_mismatch', 'Reconciliation mismatch')),
            'reconciliation_mismatch', '/portfolio/positions', 'critical'],
        // ATL-231: BackupRunner hands the Notifier this envelope when atlas:backup fails.
        'backup_failed' => ['atlas.system', notifEnvelope('system.backup_failed', 'service:backend.backup', [
            'backup_id' => (string) Str::uuid(), 'started_at' => '2026-11-02T00:00:00Z',
        ]), 'backup_failed', '/settings/backup', 'warning'],
    ];
}

function notifHandle(string $stream, array $envelope): string
{
    return app(BridgeEventHandler::class)->handle($stream, json_encode($envelope, JSON_THROW_ON_ERROR));
}

/** @return list<array{channels: list<string>, event: string, payload: array<string, mixed>}> */
function notifBroadcasts(RecordingBroadcaster $broadcasts): array
{
    return array_values(array_filter($broadcasts->sent, fn (array $b): bool => $b['event'] === 'notification.created'));
}

beforeEach(function (): void {
    $this->broadcasts = RecordingBroadcaster::install();
});

it('covers every brief §17 event, one case each', function (): void {
    expect(array_keys(notifCases()))->toEqualCanonicalizing(SettingsSchema::notificationEvents())
        ->and(SettingsSchema::notificationEvents())->toHaveCount(12);
});

it('stores exactly one notification with the right event and link, and a replay stores none', function (string $case): void {
    Event::fake([NotificationCreated::class]);
    [$stream, $envelope, $event, $link, $severity] = notifCases()[$case];

    notifHandle($stream, $envelope);

    $rows = Notification::query()->get();
    expect($rows)->toHaveCount(1);
    $row = $rows->first();
    expect($row->event)->toBe($event)
        ->and($row->severity)->toBe($severity)
        ->and($row->env)->toBe('demo')
        ->and($row->title_key)->toBe("notifications.events.{$event}.title")
        ->and($row->source_event_id)->toBe($envelope['id'])
        ->and($row->channels)->toBe(['in_app' => true, 'browser' => true, 'telegram' => false])
        ->and($row->read_at)->toBeNull()
        ->and($row->created_at->utc()->format('Y-m-d\TH:i:s.u'))->toBe('2026-11-02T09:15:00.250000');
    if (str_ends_with($link, '=')) {
        // Links to the stored War Room message.
        $messageId = $row->params['message_id'];
        expect($messageId)->toBeString()->and($row->link)->toBe($link.$messageId);
    } else {
        expect($row->link)->toBe($link);
    }

    $sent = notifBroadcasts($this->broadcasts);
    expect($sent)->toHaveCount(1)
        ->and($sent[0]['channels'])->toBe(['private-system'])
        ->and($sent[0]['payload']['id'])->toBe($row->id)
        ->and($sent[0]['payload']['event'])->toBe($event)
        ->and($sent[0]['payload']['link'])->toBe($row->link);
    Event::assertDispatchedTimes(NotificationCreated::class, 1);

    // The same envelope again (a bridge restart re-draining it): no second row, no second broadcast.
    notifHandle($stream, $envelope);
    expect(Notification::query()->count())->toBe(1)
        ->and(notifBroadcasts($this->broadcasts))->toHaveCount(1);
    Event::assertDispatchedTimes(NotificationCreated::class, 1);
})->with(array_keys(notifCases()));

it('stores one notification for an envelope read from the stream by atlas:bridge, and none when it is redelivered', function (): void {
    $redis = fn (array $args) => Redis::connection('events')->command('executeRaw', [$args]);
    $redis(['DEL', EventBus::streamKey('atlas.system')]);
    $bridge = app(Bridge::class);
    $bridge->ensureGroups();
    [$stream, $envelope] = notifCases()['kill_switch'];
    $json = json_encode($envelope, JSON_THROW_ON_ERROR);

    $redis(['XADD', EventBus::streamKey($stream), '*', EventBus::FIELD, $json]);
    $bridge->readOnce('notif-test', 10);
    $redis(['XADD', EventBus::streamKey($stream), '*', EventBus::FIELD, $json]);
    $bridge->readOnce('notif-test', 10);

    expect(Notification::query()->where('source_event_id', $envelope['id'])->count())->toBe(1)
        ->and(notifBroadcasts($this->broadcasts))->toHaveCount(1);
    $redis(['DEL', EventBus::streamKey('atlas.system')]);
});

it('stores and broadcasts nothing for order_filled with in_app and browser off', function (): void {
    Event::fake([NotificationCreated::class]);
    $settings = app(SettingsService::class);
    $row = $settings->current('demo');
    $settings->update('demo', $row->version, [
        'notifications.order_filled.in_app' => false,
        'notifications.order_filled.browser' => false,
    ], User::factory()->create());
    [$stream, $envelope] = notifCases()['order_filled'];

    notifHandle($stream, $envelope);

    expect(Notification::query()->count())->toBe(0)
        ->and(notifBroadcasts($this->broadcasts))->toBe([]);
    Event::assertNotDispatched(NotificationCreated::class);

    // Another event keeps its defaults.
    [$stream, $envelope] = notifCases()['stop_hit'];
    notifHandle($stream, $envelope);
    expect(Notification::query()->pluck('event')->all())->toBe(['stop_hit']);
});

it('stores the row for Telegram alone without broadcasting it to the browser', function (): void {
    Event::fake([NotificationCreated::class]);
    $settings = app(SettingsService::class);
    $settings->update('demo', $settings->current('demo')->version, [
        'notifications.kill_switch.in_app' => false,
        'notifications.kill_switch.browser' => false,
        'notifications.kill_switch.telegram' => true,
    ], User::factory()->create());
    [$stream, $envelope] = notifCases()['kill_switch'];

    notifHandle($stream, $envelope);

    expect(Notification::query()->sole()->channels)->toBe(['in_app' => false, 'browser' => false, 'telegram' => true])
        ->and(notifBroadcasts($this->broadcasts))->toBe([]);
    Event::assertDispatched(NotificationCreated::class, fn (NotificationCreated $e) => $e->notification->event === 'kill_switch');
});

it('ignores envelopes that are not §17 events', function (string $stream, array $envelope): void {
    notifHandle($stream, $envelope);

    expect(Notification::query()->count())->toBe(0)
        ->and(notifBroadcasts($this->broadcasts))->toBe([]);
})->with([
    'a breaker clearing' => fn () => ['atlas.system', notifEnvelope('system.circuit_breaker', 'service:trading_core.breakers', [
        'name' => 'daily_loss', 'label' => 'Daily loss limit', 'state' => 'cleared', 'reason' => null, 'detail' => [],
        'since' => null, 'self_clearing' => false, 'cleared_at' => '2026-11-02T10:00:00Z', 'cleared_by' => 'owner',
    ])],
    'an order going live' => fn () => ['atlas.orders', notifEnvelope('order.state_changed', 'service:trading_core', [
        'order_id' => NOTIF_ORDER, 'inst_id' => 'XAU-USDT-SWAP', 'origin' => 'atlas', 'from_state' => null, 'to_state' => 'live',
    ])],
    'an external order filling' => fn () => ['atlas.orders', notifEnvelope('order.state_changed', 'service:trading_core', [
        'order_id' => NOTIF_ORDER, 'inst_id' => 'XAU-USDT-SWAP', 'origin' => 'external', 'from_state' => 'live', 'to_state' => 'filled',
    ])],
    'an agent recovering' => fn () => ['atlas.system', notifEnvelope('system.agent_state', 'agent:historian', [
        'agent' => 'historian', 'state' => 'idle', 'previous_state' => 'degraded',
    ])],
    'an agent staying degraded' => fn () => ['atlas.system', notifEnvelope('system.agent_state', 'agent:historian', [
        'agent' => 'historian', 'state' => 'rate_limited', 'previous_state' => 'degraded',
    ])],
    'news on an instrument not held' => fn () => ['atlas.warroom', notifEnvelope('news.alert', 'system:news', [
        'news_item_id' => (string) Str::uuid(), 'cluster_id' => (string) Str::uuid(),
        'headline' => 'Fed speaker repeats prior view', 'source' => 'reuters', 'source_name' => 'Reuters',
        'category' => 'markets_news', 'url' => 'https://www.reuters.com/markets/fed', 'published_at' => '2026-11-02T09:14:00Z',
        'importance' => 5, 'event_type' => 'central_bank', 'inst_ids' => [], 'asset_classes' => [],
        'sentiment' => 0, 'novelty' => 0.4, 'reason' => 'importance', 'held_inst_ids' => [],
    ])],
    'an ordinary agent message' => fn () => ['atlas.warroom', notifEnvelope('warroom.message', 'agent:historian', [
        'thread' => ['kind' => 'main', 'key' => 'main'], 'author_type' => 'agent', 'author' => 'historian',
        'kind' => 'research_report', 'content_md' => 'Regime table', 'card' => ['type' => 'daily_report'],
    ])],
    'a proposal status change' => fn () => ['atlas.proposals', notifEnvelope('proposal.status_changed', 'service:trading_core', [
        'proposal_id' => NOTIF_PROPOSAL, 'from_status' => 'sent', 'to_status' => 'filled',
    ])],
]);

it('authorises private-system for a signed-in owner only', function (): void {
    config()->set('broadcasting.default', 'reverb');
    config()->set('broadcasting.connections.reverb.key', 'test-key');
    config()->set('broadcasting.connections.reverb.secret', 'test-secret');
    config()->set('broadcasting.connections.reverb.app_id', 'test');
    require base_path('routes/channels.php');
    $form = ['channel_name' => 'private-system', 'socket_id' => '1234.5678'];

    $this->post('/broadcasting/auth', $form)->assertForbidden();
    $this->actingAs(User::factory()->create())->post('/broadcasting/auth', $form)->assertOk()->assertJsonStructure(['auth']);
});

it('never puts a connection key, secret, passphrase or UID into a notification', function (string $case): void {
    $key = 'k3y-'.Str::random(28);
    $secret = 's3cr3t-'.Str::random(28);
    $passphrase = 'pass-'.Str::random(16);
    $uid = '7044'.random_int(10000000, 99999999);
    Connection::query()->create([
        'exchange' => 'okx', 'env' => 'demo', 'label' => 'Demo', 'api_key' => $key, 'api_secret' => $secret,
        'passphrase' => $passphrase, 'uid_last4' => substr($uid, -4),
    ]);
    [$stream, $envelope] = notifCases()[$case];
    // A publisher that leaks: every secret in extra fields and in a nested detail.
    $envelope['payload'] += [
        'api_key' => $key, 'api_secret' => $secret, 'passphrase' => $passphrase, 'uid' => $uid,
        'detail' => ['api_key' => $key, 'uid' => $uid], 'error' => "OKX said {$secret} for uid {$uid}",
    ];
    if ($case === 'agent_degraded') {
        $envelope['payload']['last_error'] = "auth failed with key {$key}";
    }

    notifHandle($stream, $envelope);

    $row = Notification::query()->sole();
    $stored = json_encode([$row->getAttributes(), notifBroadcasts($this->broadcasts)], JSON_THROW_ON_ERROR);
    foreach ([$key, $secret, $passphrase, $uid] as $value) {
        expect($stored)->not->toContain($value);
    }
})->with(array_keys(notifCases()));
