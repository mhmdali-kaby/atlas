<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Notification;
use App\Models\User;
use Illuminate\Support\Carbon;
use Illuminate\Support\Str;

/*
 * ATL-214: GET /api/notifications (cursor, unread=1), POST /api/notifications/{id}/read and
 * POST /api/notifications/read-all, each write audited.
 */

function makeNotification(string $at, ?string $readAt = null, string $env = 'demo', string $event = 'order_filled'): Notification
{
    return Notification::query()->create([
        'env' => $env,
        'event' => $event,
        'severity' => 'info',
        'title_key' => "notifications.events.{$event}.title",
        'params' => ['inst_id' => 'XAU-USDT-SWAP'],
        'link' => '/portfolio/orders?inst_id=XAU-USDT-SWAP',
        'source_event_id' => (string) Str::uuid(),
        'channels' => ['in_app' => true, 'browser' => true, 'telegram' => false],
        'read_at' => $readAt !== null ? Carbon::parse($readAt) : null,
        'created_at' => Carbon::parse($at),
    ]);
}

beforeEach(function (): void {
    $this->user = User::factory()->create();
});

it('refuses guests on every route', function (string $method, string $uri): void {
    $this->json($method, $uri)->assertUnauthorized();
})->with([
    ['GET', '/api/notifications'],
    ['POST', '/api/notifications/read-all'],
    ['POST', '/api/notifications/'.'0b8f1c52-7d0e-4a8e-9a39-3b4c1e0f6a11'.'/read'],
]);

it('lists the active env newest first and pages by cursor', function (): void {
    $old = makeNotification('2026-11-02T08:00:00Z');
    $mid = makeNotification('2026-11-02T09:00:00Z', '2026-11-02T09:05:00Z');
    $new = makeNotification('2026-11-02T10:00:00Z', null, 'demo', 'kill_switch');
    makeNotification('2026-11-02T11:00:00Z', null, 'live');

    $first = $this->actingAs($this->user)->getJson('/api/notifications?limit=2')->assertOk();
    expect(array_column($first->json('data'), 'id'))->toBe([$new->id, $mid->id])
        ->and($first->json('meta.env'))->toBe('demo')
        ->and($first->json('meta.unread_count'))->toBe(2)
        ->and($first->json('data.0'))->toBe([
            'id' => $new->id,
            'env' => 'demo',
            'event' => 'kill_switch',
            'severity' => 'info',
            'title_key' => 'notifications.events.kill_switch.title',
            'params' => ['inst_id' => 'XAU-USDT-SWAP'],
            'link' => '/portfolio/orders?inst_id=XAU-USDT-SWAP',
            'channels' => ['in_app' => true, 'browser' => true, 'telegram' => false],
            'source_event_id' => $new->source_event_id,
            'read_at' => null,
            'created_at' => '2026-11-02T10:00:00.000000Z',
        ]);

    $cursor = $first->json('meta.next_cursor');
    expect($cursor)->toBeString();
    $second = $this->getJson('/api/notifications?limit=2&cursor='.urlencode($cursor))->assertOk();
    expect(array_column($second->json('data'), 'id'))->toBe([$old->id])
        ->and($second->json('meta.next_cursor'))->toBeNull();
});

it('filters unread with unread=1', function (): void {
    $unread = makeNotification('2026-11-02T08:00:00Z');
    makeNotification('2026-11-02T09:00:00Z', '2026-11-02T09:05:00Z');

    $res = $this->actingAs($this->user)->getJson('/api/notifications?unread=1')->assertOk();
    expect(array_column($res->json('data'), 'id'))->toBe([$unread->id]);
});

it('rejects a crafted cursor and a bad unread flag with 422', function (string $query): void {
    $this->actingAs($this->user)->getJson('/api/notifications?'.$query)->assertUnprocessable();
})->with(['cursor=not-a-cursor', 'unread=yes', 'limit=0']);

it('marks one read and writes audit_log', function (): void {
    $n = makeNotification('2026-11-02T08:00:00Z');

    $res = $this->actingAs($this->user)->postJson("/api/notifications/{$n->id}/read")->assertOk();

    expect($res->json('data.id'))->toBe($n->id)
        ->and($res->json('data.read_at'))->toBeString()
        ->and($n->refresh()->read_at)->not->toBeNull();
    $audit = AuditLog::query()->where('action', 'notification.read')->sole();
    expect($audit->subject_type)->toBe('notifications')
        ->and($audit->subject_id)->toBe($n->id)
        ->and($audit->user_id)->toBe($this->user->id)
        ->and($audit->before)->toBe(['read_at' => null])
        ->and($audit->after['read_at'])->toBeString();
});

it('404s a notification of another env or an unknown id', function (): void {
    $live = makeNotification('2026-11-02T08:00:00Z', null, 'live');

    $this->actingAs($this->user)->postJson("/api/notifications/{$live->id}/read")->assertNotFound();
    $this->postJson('/api/notifications/'.Str::uuid().'/read')->assertNotFound();
    expect($live->refresh()->read_at)->toBeNull()
        ->and(AuditLog::query()->where('action', 'notification.read')->count())->toBe(0);
});

it('marks every unread notification of the active env read and writes audit_log', function (): void {
    $a = makeNotification('2026-11-02T08:00:00Z');
    $b = makeNotification('2026-11-02T09:00:00Z');
    $already = makeNotification('2026-11-02T07:00:00Z', '2026-11-02T07:30:00Z');
    $live = makeNotification('2026-11-02T08:00:00Z', null, 'live');

    $res = $this->actingAs($this->user)->postJson('/api/notifications/read-all')->assertOk();

    expect($res->json('data'))->toBe(['env' => 'demo', 'marked' => 2, 'unread_count' => 0])
        ->and($a->refresh()->read_at)->not->toBeNull()
        ->and($b->refresh()->read_at)->not->toBeNull()
        ->and($already->refresh()->read_at?->utc()->format('H:i'))->toBe('07:30')
        ->and($live->refresh()->read_at)->toBeNull();
    $audit = AuditLog::query()->where('action', 'notification.read_all')->sole();
    expect($audit->user_id)->toBe($this->user->id)
        ->and($audit->after['marked'])->toBe(2)
        ->and($audit->after['env'])->toBe('demo');

    $this->getJson('/api/notifications?unread=1')->assertOk()->assertJsonPath('data', []);
});
