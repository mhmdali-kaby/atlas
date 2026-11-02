<?php

declare(strict_types=1);

use App\Events\NotificationCreated;
use App\Models\AuditLog;
use App\Models\Notification;
use App\Models\Proposal;
use App\Models\TelegramConnection;
use App\Models\User;
use App\Support\Events\BridgeEventHandler;
use App\Support\Settings\SettingsService;
use App\Support\Telegram\CallbackData;
use App\Support\TradingCore;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\Request as HttpRequest;
use Illuminate\Log\Events\MessageLogged;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Event;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\Str;
use Tests\Support\RecordingBroadcaster;

/*
 * ATL-215, decision #254 (3): Telegram sends enabled notifications; its Approve / Reject buttons act
 * through ProposalApprovalService (the web Approve / Reject path) until approval_expires_at.
 * Telegram and trading_core are faked with Http::fake.
 */

const TG_TOKEN = '123456789:AAFakeTokenForPestOnly_abcdefghijWXYZ';
const TG_CHAT = '987654321';
const TG_API = 'https://api.telegram.org/bot'.TG_TOKEN;
const TG_CORE = 'http://127.0.0.1:8100';
const TG_SECRET = 'atl215-test-only-internal-secret';

function tgConnect(): TelegramConnection
{
    return TelegramConnection::query()->create([
        'bot_token' => TG_TOKEN,
        'chat_id' => TG_CHAT,
        'bot_token_last4' => 'WXYZ',
        'chat_id_last4' => '4321',
    ]);
}

function tgSetTelegram(bool $on): void
{
    $settings = app(SettingsService::class);
    $settings->update('demo', $settings->current('demo')->version, [
        'notifications.proposal_awaiting_approval.telegram' => $on,
    ], User::query()->firstOrFail());
}

/**
 * @param  array<string, mixed>  $overrides
 */
function tgProposal(array $overrides = []): Proposal
{
    $p = Proposal::query()->create(array_merge([
        'env' => 'demo', 'version' => 1, 'kind' => 'entry', 'author' => 'strategist',
        'inst_id' => 'XAU-USDT-SWAP', 'side' => 'buy', 'order_type' => 'limit',
        'entry' => '4000', 'stop' => '3960', 'take_profits' => [['price' => '4100', 'pct' => '100']],
        'size_suggestion_pct' => '5', 'leverage_suggestion' => 3, 'expected_r' => '2.5', 'probability' => '0.5',
        'rationale_md' => 'Breakout retest.', 'invalidation' => '1H close below 3960.',
        'status' => 'awaiting_owner', 'approval_expires_at' => Carbon::now()->addMinutes(10),
    ], $overrides));
    DB::table('risk_checks')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'proposal_id' => $p->id,
        'settings_version' => app(SettingsService::class)->current('demo')->version,
        'result' => 'approved', 'checks' => '[]', 'final_size' => '3', 'final_notional_usd' => '7950.30',
        'leverage' => 3, 'reason' => 'test', 'decision' => 'awaiting_owner', 'created_at' => now(), 'updated_at' => now(),
    ]);

    return $p;
}

function tgAwaitingEnvelope(Proposal $p): array
{
    return [
        'id' => (string) Str::uuid(), 'type' => 'proposal.awaiting_owner', 'ts' => Carbon::now()->utc()->toIso8601ZuluString('microsecond'),
        'env' => 'demo', 'source' => 'agent:orchestrator', 'correlation_id' => 'cycle-x',
        'payload' => [
            'proposal_id' => $p->id, 'version' => 1, 'cycle_id' => null, 'kind' => 'entry', 'inst_id' => $p->inst_id,
            'side' => 'buy', 'risk_check_id' => (string) Str::uuid(), 'result' => 'approved', 'decision' => 'awaiting_owner',
            'final_contracts' => '3', 'final_notional_usd' => '7950.30', 'leverage' => 3,
            'approval_expires_at' => $p->approval_expires_at?->utc()->toIso8601ZuluString(),
        ],
    ];
}

/**
 * @param  array<string, mixed>  $overrides
 * @return array<string, mixed>
 */
function tgCallback(int $updateId, string $data, string $chatId = TG_CHAT, ?string $fromId = null, string $chatType = 'private'): array
{
    return ['update_id' => $updateId, 'callback_query' => [
        'id' => "cq-{$updateId}", 'from' => ['id' => (int) ($fromId ?? $chatId)], 'data' => $data,
        'message' => ['message_id' => 77, 'chat' => ['id' => (int) $chatId, 'type' => $chatType], 'text' => 'Proposal awaiting your approval'],
    ]];
}

/**
 * Fake Telegram (getUpdates returns $updates once, then nothing) and trading_core execute.
 *
 * @param  list<array<string, mixed>>  $updates
 */
function tgFake(array $updates = [], int $coreStatus = 200): void
{
    $batches = [$updates];
    Http::fake([
        TG_API.'/getUpdates' => function () use (&$batches) {
            return Http::response(['ok' => true, 'result' => array_shift($batches) ?? []]);
        },
        TG_API.'/*' => Http::response(['ok' => true, 'result' => ['message_id' => 77]]),
        TG_CORE.'/*' => Http::response(['ok' => true, 'status' => 'sent', 'reason' => 'sent', 'order_id' => 'o-1', 'cl_ord_id' => 'e1', 'state' => 'live', 'risk_check' => ['risk_check_id' => 'rc-new']], $coreStatus),
    ]);
}

/** @return list<HttpRequest> */
function tgSent(string $method): array
{
    return Http::recorded(fn (HttpRequest $r): bool => $r->url() === TG_API.'/'.$method)->map(fn (array $pair) => $pair[0])->values()->all();
}

/** @return list<HttpRequest> */
function tgExecuteCalls(string $proposalId): array
{
    return Http::recorded(fn (HttpRequest $r): bool => $r->url() === TG_CORE."/internal/proposals/{$proposalId}/execute")->map(fn (array $pair) => $pair[0])->values()->all();
}

beforeEach(function (): void {
    config(['services.trading_core.url' => TG_CORE, 'services.trading_core.secret' => TG_SECRET]);
    RecordingBroadcaster::install();
    $this->user = User::factory()->create(['locale' => 'en']);
    RateLimiter::clear('telegram-write|'.$this->user->id);
    RateLimiter::clear('proposals-write|'.$this->user->id);
    $this->logs = [];
    Event::listen(MessageLogged::class, function (MessageLogged $m): void {
        $this->logs[] = json_encode([$m->message, $m->context], JSON_PARTIAL_OUTPUT_ON_ERROR);
    });
});

// --- AC1: sending -------------------------------------------------------------------------------

it('sends one sendMessage with Approve and Reject buttons when the telegram toggle is on', function (): void {
    tgFake();
    tgConnect();
    tgSetTelegram(true);
    $p = tgProposal();

    app(BridgeEventHandler::class)->handle('atlas.proposals', json_encode(tgAwaitingEnvelope($p), JSON_THROW_ON_ERROR));

    $sent = tgSent('sendMessage');
    expect($sent)->toHaveCount(1);
    $body = $sent[0]->data();
    expect($body['chat_id'])->toBe(TG_CHAT)
        ->and($body['text'])->toContain('[DEMO] Proposal awaiting your approval')
        ->and($body['text'])->toContain('XAU-USDT-SWAP')
        ->and($body['text'])->toContain('http://localhost:3000/warroom?proposal='.$p->id)
        ->and($body)->not->toHaveKey('parse_mode');
    $buttons = $body['reply_markup']['inline_keyboard'][0];
    expect(array_column($buttons, 'text'))->toBe(['Approve', 'Reject']);
    foreach ($buttons as $i => $button) {
        expect(strlen($button['callback_data']))->toBeLessThanOrEqual(64)
            ->and(str_starts_with($button['callback_data'], $i === 0 ? 'a:' : 'r:'))->toBeTrue();
        $decoded = CallbackData::decode($button['callback_data']);
        expect($decoded)->toBe([
            'action' => $i === 0 ? 'a' : 'r',
            'proposal_id' => $p->id,
            'expires_unix' => $p->approval_expires_at?->getTimestamp(),
        ]);
    }
    expect(TelegramConnection::current()?->last_sent_at)->not->toBeNull();
});

it('sends nothing when the telegram toggle is off', function (): void {
    tgFake();
    tgConnect();
    tgSetTelegram(false);
    $p = tgProposal();

    app(BridgeEventHandler::class)->handle('atlas.proposals', json_encode(tgAwaitingEnvelope($p), JSON_THROW_ON_ERROR));

    expect(Notification::query()->sole()->channels['telegram'])->toBeFalse();
    Http::assertNothingSent();
});

it('writes the message in Arabic for an Arabic-speaking owner, with no buttons for other events', function (): void {
    tgFake();
    tgConnect();
    $this->user->forceFill(['locale' => 'ar'])->save();
    $n = Notification::query()->create([
        'env' => 'demo', 'event' => 'kill_switch', 'severity' => 'critical', 'title_key' => 'notifications.events.kill_switch.title',
        'params' => ['flatten' => true, 'ok' => true, 'cancelled' => 4, 'closed' => 2, 'stops_kept' => 2, 'failed' => 0],
        'link' => '/warroom', 'source_event_id' => (string) Str::uuid(), 'channels' => ['in_app' => false, 'browser' => false, 'telegram' => true],
        'created_at' => Carbon::now(),
    ]);

    event(new NotificationCreated($n));

    $body = tgSent('sendMessage')[0]->data();
    expect($body['text'])->toContain('[تجريبي] تم استخدام زر الإيقاف')
        ->and($body['text'])->toContain('أوامر أُلغيت: 4')
        ->and($body)->not->toHaveKey('reply_markup');
});

it('fits callback_data in 64 bytes and refuses a tampered one', function (): void {
    $id = (string) Str::uuid();
    $data = CallbackData::encode('a', $id, 1_900_000_000);
    expect(strlen($data))->toBeLessThanOrEqual(64)
        ->and(CallbackData::decode($data))->toBe(['action' => 'a', 'proposal_id' => $id, 'expires_unix' => 1_900_000_000])
        ->and(CallbackData::decode('r'.substr($data, 1)))->toBeNull()
        ->and(CallbackData::decode(str_replace(':1900000000:', ':1900000999:', $data)))->toBeNull();
    config(['app.key' => 'base64:'.base64_encode(random_bytes(32))]);
    expect(CallbackData::decode($data))->toBeNull();
});

// --- AC2: valid presses ---------------------------------------------------------------------------

it('approves a valid Approve press through the same execute call as the web approve, audited as telegram', function (): void {
    tgConnect();
    $p = tgProposal();
    $web = tgProposal();
    tgFake([tgCallback(500, CallbackData::encode('a', $p->id, $p->approval_expires_at->getTimestamp()))]);

    $this->artisan('atlas:telegram-poll', ['--once' => true])->assertSuccessful();
    $this->actingAs($this->user, 'web')->postJson("/api/proposals/{$web->id}/approve")->assertOk();

    $fromTelegram = tgExecuteCalls($p->id);
    $fromWeb = tgExecuteCalls($web->id);
    expect($fromTelegram)->toHaveCount(1)->and($fromWeb)->toHaveCount(1)
        ->and($fromTelegram[0]->method())->toBe($fromWeb[0]->method())
        ->and($fromTelegram[0]->body())->toBe($fromWeb[0]->body())
        ->and($fromTelegram[0]->body())->toBe('{"approved_by":"owner"}');
    $ts = $fromTelegram[0]->header(TradingCore::TIMESTAMP_HEADER)[0];
    $nonce = $fromTelegram[0]->header(TradingCore::NONCE_HEADER)[0];
    expect($fromTelegram[0]->header(TradingCore::SIGNATURE_HEADER)[0])
        ->toBe(TradingCore::sign(TG_SECRET, $ts, $nonce, 'POST', "/internal/proposals/{$p->id}/execute", $fromTelegram[0]->body()));

    $audit = AuditLog::query()->where('action', 'proposal.approved')->where('subject_id', $p->id)->sole();
    expect($audit->after)->toMatchArray(['actor' => 'telegram', 'ok' => true, 'order_id' => 'o-1'])
        ->and($audit->user_id)->toBe($this->user->id)
        ->and(AuditLog::query()->where('action', 'proposal.approved')->where('subject_id', $web->id)->sole()->after['actor'])->toBe('owner');

    $answer = tgSent('answerCallbackQuery');
    expect($answer)->toHaveCount(1)->and($answer[0]->data())->toBe(['callback_query_id' => 'cq-500', 'text' => 'Approved — the order was sent.']);
    $edit = tgSent('editMessageText');
    expect($edit)->toHaveCount(1)
        ->and($edit[0]->data()['message_id'])->toBe(77)
        ->and($edit[0]->data()['text'])->toContain('Approved from Telegram at')
        ->and($edit[0]->data()['reply_markup'])->toBe(['inline_keyboard' => []]);
    expect(TelegramConnection::current()?->update_offset)->toBe(500);
});

it('rejects a valid Reject press through the shared service, audited as telegram', function (): void {
    tgConnect();
    $p = tgProposal();
    tgFake([tgCallback(501, CallbackData::encode('r', $p->id, $p->approval_expires_at->getTimestamp()))]);

    $this->artisan('atlas:telegram-poll', ['--once' => true])->assertSuccessful();

    $p->refresh();
    expect($p->status)->toBe('rejected')
        ->and($p->status_reason)->toBe('owner_rejected')
        ->and($p->owner_note)->toBe('Rejected from Telegram.')
        ->and(tgExecuteCalls($p->id))->toBe([]);
    expect(AuditLog::query()->where('action', 'proposal.rejected')->sole()->after)->toMatchArray(['actor' => 'telegram', 'status' => 'rejected']);
    expect(tgSent('answerCallbackQuery')[0]->data()['text'])->toBe('Rejected.')
        ->and(tgSent('editMessageText')[0]->data()['text'])->toContain('Rejected from Telegram at');
});

// --- AC3: refused presses -------------------------------------------------------------------------

it('changes nothing and answers "expired" for a wrong HMAC or a press after approval_expires_at', function (string $case): void {
    tgConnect();
    $p = tgProposal();
    $exp = $p->approval_expires_at->getTimestamp();
    $data = CallbackData::encode('a', $p->id, $exp);
    if ($case === 'wrong_hmac') {
        $data = substr($data, 0, -4).(str_ends_with($data, 'AAAA') ? 'BBBB' : 'AAAA');
    }
    tgFake([tgCallback(600, $data)]);
    if ($case === 'expired') {
        Carbon::setTestNow(Carbon::createFromTimestamp($exp + 1));
    }

    $this->artisan('atlas:telegram-poll', ['--once' => true])->assertSuccessful();
    Carbon::setTestNow();

    expect($p->fresh()?->status)->toBe('awaiting_owner')
        ->and(tgExecuteCalls($p->id))->toBe([])
        ->and(AuditLog::query()->whereIn('action', ['proposal.approved', 'proposal.rejected'])->count())->toBe(0)
        ->and(AuditLog::query()->where('action', 'telegram.callback_refused')->sole()->after['actor'])->toBe('telegram');
    expect(tgSent('answerCallbackQuery'))->toHaveCount(1)
        ->and(tgSent('answerCallbackQuery')[0]->data()['text'])->toBe('expired')
        ->and(tgSent('editMessageText'))->toBe([]);
})->with(['wrong_hmac', 'expired']);

it('answers "expired" when the button outlived its proposal (already rejected on the web)', function (): void {
    tgConnect();
    $p = tgProposal();
    $data = CallbackData::encode('a', $p->id, $p->approval_expires_at->getTimestamp());
    $this->actingAs($this->user, 'web')->postJson("/api/proposals/{$p->id}/reject", ['reason' => 'No.'])->assertOk();
    tgFake([tgCallback(601, $data)]);

    $this->artisan('atlas:telegram-poll', ['--once' => true])->assertSuccessful();

    expect($p->fresh()?->status)->toBe('rejected')->and(tgExecuteCalls($p->id))->toBe([])
        ->and(tgSent('answerCallbackQuery')[0]->data()['text'])->toBe('expired');
});

// ATL-221 (security round 16): only the owner's own private chat may press Approve/Reject.

it('refuses a validly signed callback from a different Telegram user, auditing foreign_user', function (): void {
    tgConnect();
    $p = tgProposal();
    tgFake([tgCallback(602, CallbackData::encode('a', $p->id, $p->approval_expires_at->getTimestamp()), fromId: '111222333')]);

    $this->artisan('atlas:telegram-poll', ['--once' => true])->assertSuccessful();

    expect($p->fresh()?->status)->toBe('awaiting_owner')
        ->and(tgExecuteCalls($p->id))->toBe([])
        ->and(tgSent('answerCallbackQuery'))->toHaveCount(1)
        ->and(tgSent('answerCallbackQuery')[0]->data()['text'])->toBe('expired')
        ->and(tgSent('editMessageText'))->toBe([])
        ->and(AuditLog::query()->where('action', 'telegram.callback_refused')->sole()->after)
        ->toMatchArray(['actor' => 'telegram', 'reason' => 'foreign_user'])
        ->and(TelegramConnection::current()?->update_offset)->toBe(602);
});

it('refuses a validly signed callback from a group chat, auditing foreign_user', function (): void {
    tgConnect();
    $p = tgProposal();
    tgFake([tgCallback(603, CallbackData::encode('a', $p->id, $p->approval_expires_at->getTimestamp()), chatType: 'supergroup')]);

    $this->artisan('atlas:telegram-poll', ['--once' => true])->assertSuccessful();

    expect($p->fresh()?->status)->toBe('awaiting_owner')
        ->and(tgExecuteCalls($p->id))->toBe([])
        ->and(tgSent('answerCallbackQuery')[0]->data()['text'])->toBe('expired')
        ->and(AuditLog::query()->where('action', 'telegram.callback_refused')->sole()->after)
        ->toMatchArray(['actor' => 'telegram', 'reason' => 'foreign_user']);
});

// --- AC5: offset ------------------------------------------------------------------------------------

it('resumes from the stored offset and never handles an update twice', function (): void {
    $conn = tgConnect();
    $conn->forceFill(['update_offset' => 700])->save();
    $old = tgProposal();
    $new = tgProposal();
    $redelivered = tgCallback(700, CallbackData::encode('a', $old->id, $old->approval_expires_at->getTimestamp()));
    $fresh = tgCallback(701, CallbackData::encode('a', $new->id, $new->approval_expires_at->getTimestamp()));
    // Telegram re-sends 700 (it was never confirmed) beside the new 701, out of order.
    $batches = [[$fresh, $redelivered], [$fresh, $redelivered]];
    Http::fake([
        TG_API.'/getUpdates' => function () use (&$batches) {
            return Http::response(['ok' => true, 'result' => array_shift($batches) ?? []]);
        },
        TG_API.'/*' => Http::response(['ok' => true, 'result' => true]),
        TG_CORE.'/*' => Http::response(['ok' => true, 'status' => 'sent', 'order_id' => 'o-2'], 200),
    ]);

    $this->artisan('atlas:telegram-poll', ['--once' => true])->expectsOutput('Taken 1 update(s).')->assertSuccessful();
    // A restart, and the same batch again.
    $this->artisan('atlas:telegram-poll', ['--once' => true])->expectsOutput('Taken 0 update(s).')->assertSuccessful();

    $polls = tgSent('getUpdates');
    expect($polls)->toHaveCount(2)
        ->and($polls[0]->data()['offset'])->toBe(701)
        ->and($polls[1]->data()['offset'])->toBe(702)
        ->and($polls[0]->data()['allowed_updates'])->toBe(['callback_query']);
    expect(tgExecuteCalls($new->id))->toHaveCount(1)
        ->and(tgExecuteCalls($old->id))->toBe([])
        ->and(TelegramConnection::current()?->update_offset)->toBe(701)
        ->and(AuditLog::query()->where('action', 'proposal.approved')->count())->toBe(1);
});

it('starts with no offset after a new bot token is saved', function (): void {
    $conn = tgConnect();
    $conn->forceFill(['update_offset' => 900])->save();

    $this->actingAs($this->user, 'web')->putJson('/api/notifications/telegram', ['bot_token' => '222333444:BBotherTokenForPestOnly_abcdefghij1234', 'chat_id' => TG_CHAT])->assertOk();
    expect(TelegramConnection::current()?->update_offset)->toBeNull();
});

// --- AC4: the token never comes back ---------------------------------------------------------------

it('stores the token and chat id encrypted and returns only their last 4 characters', function (): void {
    Http::fake([TG_API.'/*' => Http::response(['ok' => true, 'result' => ['message_id' => 1]])]);
    $this->actingAs($this->user, 'web')->getJson('/api/notifications/telegram')->assertOk()->assertJsonPath('data.configured', false);

    $put = $this->actingAs($this->user, 'web')->putJson('/api/notifications/telegram', ['bot_token' => TG_TOKEN, 'chat_id' => TG_CHAT])
        ->assertCreated()
        ->assertJsonPath('data.configured', true)
        ->assertJsonPath('data.bot_token_last4', 'WXYZ')
        ->assertJsonPath('data.chat_id_last4', '4321');
    $get = $this->actingAs($this->user, 'web')->getJson('/api/notifications/telegram')->assertOk();
    $test = $this->actingAs($this->user, 'web')->postJson('/api/notifications/telegram/test')->assertOk()->assertJsonPath('ok', true);

    foreach ([$put, $get, $test] as $response) {
        expect($response->getContent())->not->toContain(TG_TOKEN)
            ->and($response->getContent())->not->toContain(TG_CHAT)
            ->and($response->getContent())->not->toContain('AAFakeToken');
    }
    $raw = DB::table('telegram_connections')->sole();
    expect($raw->bot_token)->not->toContain('AAFakeToken')->and($raw->chat_id)->not->toBe(TG_CHAT);
    expect(json_encode(AuditLog::query()->get()->toArray()))->not->toContain('AAFakeToken')->not->toContain(TG_CHAT);
    expect(tgSent('sendMessage')[0]->data()['text'])->toBe('Atlas test message: Telegram notifications are connected.');

    $this->actingAs($this->user, 'web')->deleteJson('/api/notifications/telegram')->assertOk()->assertJsonPath('data.configured', false);
    expect(TelegramConnection::query()->count())->toBe(0);
});

it('validates the token and chat id, and refuses guests', function (): void {
    $this->putJson('/api/notifications/telegram', ['bot_token' => TG_TOKEN, 'chat_id' => TG_CHAT])->assertUnauthorized();
    $this->getJson('/api/notifications/telegram')->assertUnauthorized();
    $this->actingAs($this->user, 'web')->putJson('/api/notifications/telegram', ['bot_token' => 'nope', 'chat_id' => 'abc'])
        ->assertStatus(422)->assertJsonValidationErrors(['bot_token', 'chat_id']);
    $this->actingAs($this->user, 'web')->postJson('/api/notifications/telegram/test')->assertStatus(409)->assertJsonPath('error', 'not_configured');
});

// ATL-221 (security round 16): a group or supergroup chat id (negative) is refused at the door.
it('refuses a negative (group) chat_id', function (): void {
    $this->actingAs($this->user, 'web')->putJson('/api/notifications/telegram', ['bot_token' => TG_TOKEN, 'chat_id' => '-1001234567890'])
        ->assertStatus(422)->assertJsonValidationErrors(['chat_id']);
    expect(TelegramConnection::query()->count())->toBe(0);
});

it('keeps the token out of logs, exceptions and stored errors when Telegram fails', function (string $failure): void {
    tgConnect();
    Http::fake([TG_API.'/*' => $failure === 'network'
        ? fn () => throw new ConnectionException('cURL error 7: Failed to connect for '.TG_API.'/sendMessage')
        : Http::response(['ok' => false, 'error_code' => 401, 'description' => 'Unauthorized '.TG_TOKEN], 401)]);

    $test = $this->actingAs($this->user, 'web')->postJson('/api/notifications/telegram/test')->assertStatus(502)->assertJsonPath('error', 'test_failed');
    $get = $this->actingAs($this->user, 'web')->getJson('/api/notifications/telegram')->assertOk();

    $n = Notification::query()->create([
        'env' => 'demo', 'event' => 'order_filled', 'severity' => 'info', 'title_key' => 'notifications.events.order_filled.title',
        'params' => ['inst_id' => 'BTC-USDT-SWAP', 'side' => 'buy', 'purpose' => 'entry', 'filled_sz' => '1', 'avg_px' => '1'],
        'link' => null, 'source_event_id' => (string) Str::uuid(), 'channels' => ['in_app' => true, 'browser' => true, 'telegram' => true],
        'created_at' => Carbon::now(),
    ]);
    $thrown = null;
    try {
        event(new NotificationCreated($n));
    } catch (Throwable $e) {
        $thrown = $e;
    }
    expect($thrown)->not->toBeNull()
        ->and($thrown?->getMessage())->not->toContain(TG_TOKEN)
        ->and($thrown?->getPrevious())->toBeNull();

    $polled = $this->artisan('atlas:telegram-poll', ['--once' => true]);
    $polled->assertFailed();

    foreach ([$test->getContent(), $get->getContent(), (string) TelegramConnection::current()?->last_error, implode("\n", $this->logs)] as $text) {
        expect($text)->not->toContain(TG_TOKEN)->and($text)->not->toContain('AAFakeToken');
    }
    expect($this->logs)->not->toBe([]);
    if ($failure === 'http') {
        // Telegram echoing the token back in its error text is scrubbed too.
        expect(TelegramConnection::current()?->last_error)->toContain('[redacted]');
    }
})->with(['network', 'http']);

// ATL-238, decision #255 (7): a live proposal is approved only in Atlas; Reject still works.

it('refuses Telegram Approve on a live proposal with "approve in Atlas", auditing it, and keeps it awaiting_owner', function (): void {
    tgConnect();
    $p = tgProposal(['env' => 'live']);
    tgFake([tgCallback(700, CallbackData::encode('a', $p->id, $p->approval_expires_at->getTimestamp()))]);

    $this->artisan('atlas:telegram-poll', ['--once' => true])->assertSuccessful();

    expect($p->fresh()?->status)->toBe('awaiting_owner')
        ->and(tgExecuteCalls($p->id))->toBe([])
        ->and(AuditLog::query()->where('action', 'proposal.approved')->exists())->toBeFalse()
        ->and(AuditLog::query()->where('action', 'telegram.callback_refused')->sole()->after)
        ->toMatchArray(['actor' => 'telegram', 'reason' => 'live_approve_in_atlas'])
        ->and(AuditLog::query()->where('action', 'telegram.callback_refused')->sole()->subject_id)->toBe($p->id);
    expect(tgSent('answerCallbackQuery'))->toHaveCount(1)
        ->and(tgSent('answerCallbackQuery')[0]->data()['text'])->toBe('Live trade: approve it in Atlas. Reject still works here.')
        ->and(tgSent('editMessageText'))->toBe([]);
});

it('rejects a live proposal from Telegram', function (): void {
    tgConnect();
    $p = tgProposal(['env' => 'live']);
    tgFake([tgCallback(701, CallbackData::encode('r', $p->id, $p->approval_expires_at->getTimestamp()))]);

    $this->artisan('atlas:telegram-poll', ['--once' => true])->assertSuccessful();

    expect($p->fresh()?->status)->toBe('rejected')
        ->and(tgExecuteCalls($p->id))->toBe([])
        ->and(AuditLog::query()->where('action', 'proposal.rejected')->sole()->after)->toMatchArray(['actor' => 'telegram', 'status' => 'rejected'])
        ->and(tgSent('answerCallbackQuery')[0]->data()['text'])->toBe('Rejected.');
});
