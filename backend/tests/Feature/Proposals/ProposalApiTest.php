<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Proposal;
use App\Models\User;
use App\Models\WarroomMessage;
use App\Support\TradingCore;
use Illuminate\Http\Client\Request as HttpRequest;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Str;

/*
 * ATL-162: /api/proposals — approve makes one signed execute call to trading_core (faked here),
 * reject records the reason, edit only tightens the stop and re-runs the risk check, ask-team
 * mentions the Orchestrator. trading_core's own re-check is tested in pytest.
 */

const ATL162_CORE = 'http://127.0.0.1:8100';
const ATL162_SECRET = 'atl162-test-only-internal-secret';

function atl162Settings(int $version): void
{
    DB::table('settings')->updateOrInsert(['env' => 'demo'], [
        'id' => DB::table('settings')->where('env', 'demo')->value('id') ?? (string) Str::uuid(),
        'version' => $version,
        'data' => json_encode(['risk_profile' => 'balanced', 'autonomy_level' => 2]),
        'risk_profile' => 'balanced',
        'autonomy_level' => 2,
        'max_loss_per_trade_pct' => 1,
        'max_position_pct' => 5,
        'max_leverage' => 5,
        'created_at' => now(),
        'updated_at' => now(),
    ]);
}

/**
 * @param  array<string, mixed>  $overrides
 */
function atl162Proposal(array $overrides = []): Proposal
{
    return Proposal::query()->create(array_merge([
        'env' => 'demo',
        'version' => 1,
        'kind' => 'entry',
        'author' => 'strategist',
        'inst_id' => 'BTC-USDT-SWAP',
        'side' => 'buy',
        'order_type' => 'limit',
        'entry' => '4000',
        'stop' => '3960',
        'take_profits' => [['price' => '4100', 'pct' => '100']],
        'size_suggestion_pct' => '5',
        'leverage_suggestion' => 3,
        'expected_r' => '2.5',
        'probability' => '0.5',
        'rationale_md' => 'Breakout retest.',
        'invalidation' => '1H close below 3960.',
        'status' => 'awaiting_owner',
        'approval_expires_at' => Carbon::now()->addMinutes(10),
    ], $overrides));
}

function atl162RiskCheck(Proposal $p, int $settingsVersion, string $result = 'resized'): string
{
    $id = (string) Str::uuid();
    DB::table('risk_checks')->insert([
        'id' => $id,
        'env' => $p->env,
        'proposal_id' => $p->id,
        'settings_version' => $settingsVersion,
        'result' => $result,
        'checks' => '[]',
        'final_size' => $result === 'rejected' ? null : '3',
        'final_notional_usd' => $result === 'rejected' ? null : '300',
        'leverage' => $result === 'rejected' ? null : 2,
        'reason' => 'test',
        'decision' => $result === 'rejected' ? null : 'awaiting_owner',
        'created_at' => now(),
        'updated_at' => now(),
    ]);

    return $id;
}

/** @return list<array<string, mixed>> */
function atl162Events(string $stream): array
{
    $out = [];
    foreach (Redis::connection('events')->xrange($stream, '-', '+') ?: [] as $fields) {
        $out[] = json_decode((string) $fields['envelope'], true);
    }

    return $out;
}

beforeEach(function (): void {
    config(['services.trading_core.url' => ATL162_CORE, 'services.trading_core.secret' => ATL162_SECRET]);
    $this->user = User::factory()->create();
    RateLimiter::clear('proposals-write|'.$this->user->id);
    Redis::connection('events')->del('atlas.proposals', 'atlas.warroom');
    atl162Settings(7);
});

function atl162Signed(HttpRequest $request, string $path): bool
{
    $ts = $request->header(TradingCore::TIMESTAMP_HEADER)[0] ?? '';
    $nonce = $request->header(TradingCore::NONCE_HEADER)[0] ?? '';
    $sig = TradingCore::sign(ATL162_SECRET, $ts, $nonce, $request->method(), $path, $request->body());

    return $request->url() === ATL162_CORE.$path && hash_equals($sig, $request->header(TradingCore::SIGNATURE_HEADER)[0] ?? '');
}

// --- AC5 ---------------------------------------------------------------------------------------

it('refuses an unauthenticated approve with 401 and calls nothing', function (): void {
    Http::fake();
    $p = atl162Proposal();
    atl162RiskCheck($p, 7);

    $this->postJson("/api/proposals/{$p->id}/approve")->assertUnauthorized();
    $this->getJson('/api/proposals')->assertUnauthorized();
    $this->postJson("/api/proposals/{$p->id}/reject", ['reason' => 'x'])->assertUnauthorized();
    $this->patchJson("/api/proposals/{$p->id}", ['stop' => '3970'])->assertUnauthorized();
    Http::assertNothingSent();
    expect($p->fresh()?->status)->toBe('awaiting_owner');
});

// --- AC1 ---------------------------------------------------------------------------------------

it('approves an awaiting_owner proposal with one signed execute call and an audit row', function (): void {
    $p = atl162Proposal();
    $rc = atl162RiskCheck($p, 7);
    $path = "/internal/proposals/{$p->id}/execute";
    Http::fake([ATL162_CORE.$path => Http::response([
        'ok' => true, 'status' => 'sent', 'reason' => 'sent', 'detail' => 'Order sent; stop confirmed on OKX.',
        'risk_check_id' => 'new-rc', 'order_id' => 'o-1', 'cl_ord_id' => 'e123', 'ord_id' => '900001',
        'state' => 'live', 'stop_verified' => true, 'okx_code' => null, 'proposal_id' => $p->id,
        'risk_check' => ['risk_check_id' => 'new-rc', 'result' => 'resized'],
    ])]);

    $this->actingAs($this->user, 'web')
        ->postJson("/api/proposals/{$p->id}/approve")
        ->assertOk()
        ->assertJsonPath('ok', true)
        ->assertJsonPath('execution.status', 'sent')
        ->assertJsonPath('execution.order_id', 'o-1');

    Http::assertSentCount(1);
    Http::assertSent(fn (HttpRequest $r): bool => atl162Signed($r, $path)
        && $r->method() === 'POST'
        && json_decode($r->body(), true) === ['approved_by' => 'owner']);

    $audit = AuditLog::query()->where('action', 'proposal.approved')->sole();
    expect($audit->user_id)->toBe($this->user->id)
        ->and($audit->subject_id)->toBe($p->id)
        ->and($audit->before)->toMatchArray(['risk_check_id' => $rc])
        ->and($audit->after)->toMatchArray(['ok' => true, 'status' => 'sent', 'order_id' => 'o-1', 'risk_check_id' => 'new-rc']);

    $events = array_values(array_filter(atl162Events('atlas.proposals'), fn (array $e): bool => $e['type'] === 'proposal.approved'));
    expect($events)->toHaveCount(1)
        ->and($events[0]['payload']['proposal_id'])->toBe($p->id)
        ->and($events[0]['source'])->toBe('human:owner');
});

it('returns 409 and makes no call for an expired, rejected or settings-changed proposal', function (string $case): void {
    Http::fake();
    $p = match ($case) {
        'expired' => atl162Proposal(['approval_expires_at' => Carbon::now()->subSecond()]),
        'rejected' => atl162Proposal(['status' => 'rejected', 'status_reason' => 'owner_rejected', 'approval_expires_at' => null]),
        'settings_changed' => atl162Proposal(),
    };
    atl162RiskCheck($p, 7);
    if ($case === 'settings_changed') {
        atl162Settings(8);
    }
    $expected = ['expired' => 'approval_expired', 'rejected' => 'not_awaiting_owner', 'settings_changed' => 'settings_changed'][$case];

    $this->actingAs($this->user, 'web')
        ->postJson("/api/proposals/{$p->id}/approve")
        ->assertStatus(409)
        ->assertJsonPath('error', $expected)
        ->assertJsonPath('proposal.actions.approve', false);

    Http::assertNothingSent();
    expect(AuditLog::query()->where('action', 'proposal.approved')->exists())->toBeFalse();
})->with(['expired', 'rejected', 'settings_changed']);

it('passes a trading_core refusal through as 409', function (): void {
    $p = atl162Proposal();
    atl162RiskCheck($p, 7);
    Http::fake([ATL162_CORE.'/*' => Http::response([
        'ok' => false, 'status' => 'refused', 'reason' => 'risk_rejected', 'detail' => 'Stop too tight.',
        'proposal_id' => $p->id, 'risk_check' => ['result' => 'rejected'],
    ], 409)]);

    $this->actingAs($this->user, 'web')
        ->postJson("/api/proposals/{$p->id}/approve")
        ->assertStatus(409)
        ->assertJsonPath('error', 'execution_refused')
        ->assertJsonPath('execution.reason', 'risk_rejected');
    Http::assertSentCount(1);
});

// --- reject --------------------------------------------------------------------------------------

it('rejects with the owner reason, audited and published', function (): void {
    Http::fake();
    $p = atl162Proposal();

    $this->actingAs($this->user, 'web')
        ->postJson("/api/proposals/{$p->id}/reject", ['reason' => 'CPI in an hour.'])
        ->assertOk()
        ->assertJsonPath('proposal.status', 'rejected')
        ->assertJsonPath('proposal.status_reason', 'owner_rejected')
        ->assertJsonPath('proposal.owner_note', 'CPI in an hour.');

    expect(AuditLog::query()->where('action', 'proposal.rejected')->sole()->after)->toMatchArray(['reason' => 'CPI in an hour.'])
        ->and(collect(atl162Events('atlas.proposals'))->pluck('type')->all())->toBe(['proposal.rejected']);
    $this->actingAs($this->user, 'web')->postJson("/api/proposals/{$p->id}/reject", ['reason' => 'again'])
        ->assertStatus(409)->assertJsonPath('error', 'not_rejectable');
    $this->actingAs($this->user, 'web')->postJson("/api/proposals/{$p->id}/reject", [])
        ->assertStatus(422)->assertJsonValidationErrors('reason');
    Http::assertNothingSent();
});

// --- AC2: edit -------------------------------------------------------------------------------------

it('refuses with 422 an edit that moves a buy stop further from the entry', function (): void {
    Http::fake();
    $p = atl162Proposal();

    $this->actingAs($this->user, 'web')
        ->patchJson("/api/proposals/{$p->id}", ['stop' => '3950'])
        ->assertStatus(422)
        ->assertJsonValidationErrors(['stop' => __('proposals.stop_widened')]);

    $this->actingAs($this->user, 'web')
        ->patchJson("/api/proposals/{$p->id}", ['stop' => '4000'])
        ->assertStatus(422)
        ->assertJsonValidationErrors(['stop' => __('proposals.stop_wrong_side')]);

    Http::assertNothingSent();
    expect(Proposal::query()->count())->toBe(1)
        ->and($p->fresh()?->status)->toBe('awaiting_owner');
});

it('refuses with 422 a sell stop moved up, away from the entry', function (): void {
    Http::fake();
    $p = atl162Proposal(['side' => 'sell', 'stop' => '4040', 'take_profits' => [['price' => '3900', 'pct' => '100']]]);

    $this->actingAs($this->user, 'web')
        ->patchJson("/api/proposals/{$p->id}", ['stop' => '4050'])
        ->assertStatus(422)
        ->assertJsonValidationErrors('stop');
    Http::assertNothingSent();
});

it('creates version 2 with a new risk check when the edit tightens the stop', function (): void {
    $p = atl162Proposal();
    $old = atl162RiskCheck($p, 7);
    $newCheck = null;
    Http::fake(function (HttpRequest $request) use (&$newCheck) {
        // Stand-in for trading_core's evaluate: one risk_checks row on the revision.
        $revisionId = (string) Str::afterLast($request->url(), '/');
        $revision = Proposal::query()->findOrFail($revisionId);
        expect($revision->status)->toBe('passed');
        $newCheck = atl162RiskCheck($revision, 7);
        Proposal::query()->whereKey($revisionId)->update(['status' => 'risk_checked']);

        return Http::response([
            'ok' => true, 'proposal_id' => $revisionId, 'risk_check_id' => $newCheck, 'env' => 'demo',
            'settings_version' => 7, 'result' => 'resized', 'decision' => 'awaiting_owner', 'reason' => 'ok',
            'failed_check' => null, 'binding_cap' => null, 'final_contracts' => '3', 'final_notional_usd' => '300',
            'leverage' => 2, 'checks' => [],
        ]);
    });

    $res = $this->actingAs($this->user, 'web')
        ->patchJson("/api/proposals/{$p->id}", [
            'stop' => '3975',
            'size' => '2.5',
            'targets' => [['price' => '4100', 'pct' => '50'], ['price' => '4150', 'pct' => '50']],
        ])
        ->assertOk()
        ->assertJsonPath('proposal.version', 2)
        ->assertJsonPath('proposal.parent_id', $p->id)
        ->assertJsonPath('proposal.stop', '3975')
        ->assertJsonPath('proposal.size_suggestion_pct', '2.5')
        ->assertJsonPath('proposal.status', 'awaiting_owner')
        ->assertJsonPath('proposal.author', 'owner')
        ->assertJsonPath('proposal.actions.approve', true)
        ->assertJsonCount(2, 'proposal.versions');

    $revisionId = $res->json('proposal.id');
    Http::assertSentCount(1);
    Http::assertSent(fn (HttpRequest $r): bool => atl162Signed($r, "/internal/risk/evaluate/{$revisionId}"));

    $revision = Proposal::query()->findOrFail($revisionId);
    expect($revision->version)->toBe(2)
        ->and($revision->approval_expires_at?->equalTo($p->approval_expires_at))->toBeTrue()
        ->and($revision->take_profits)->toEqual([['price' => '4100', 'pct' => '50'], ['price' => '4150', 'pct' => '50']])
        ->and(DB::table('risk_checks')->where('proposal_id', $revisionId)->pluck('id')->all())->toBe([$newCheck])
        ->and(DB::table('risk_checks')->where('proposal_id', $p->id)->pluck('id')->all())->toBe([$old])
        ->and($p->fresh()?->status)->toBe('cancelled')
        ->and($p->fresh()?->status_reason)->toBe('revised')
        ->and(AuditLog::query()->where('action', 'proposal.edited')->sole()->after)->toMatchArray(['stop' => '3975', 'version' => 2])
        ->and(collect(atl162Events('atlas.proposals'))->pluck('type')->all())->toBe(['proposal.revised']);

    // The old version can no longer be approved.
    $this->actingAs($this->user, 'web')->postJson("/api/proposals/{$p->id}/approve")
        ->assertStatus(409)->assertJsonPath('error', 'not_awaiting_owner');
});

it('puts the original back when the re-check cannot run', function (): void {
    $p = atl162Proposal();
    Http::fake([ATL162_CORE.'/*' => Http::response(['ok' => false, 'error' => 'risk_engine_error', 'detail' => 'no settings'], 409)]);

    $this->actingAs($this->user, 'web')
        ->patchJson("/api/proposals/{$p->id}", ['stop' => '3970'])
        ->assertStatus(409)
        ->assertJsonPath('error', 'risk_engine_unavailable');

    expect(Proposal::query()->count())->toBe(1)
        ->and($p->fresh()?->status)->toBe('awaiting_owner')
        ->and($p->fresh()?->stop)->toStartWith('3960');
});

// --- list / show / ask-team -------------------------------------------------------------------------

it('lists with filters and shows votes, the DA review and the risk check', function (): void {
    $p = atl162Proposal();
    atl162RiskCheck($p, 7);
    atl162Proposal(['inst_id' => 'ETH-USDT-SWAP', 'status' => 'rejected', 'approval_expires_at' => null]);
    DB::table('votes')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'proposal_id' => $p->id, 'agent' => 'historian',
        'vote' => 'take', 'confidence' => '0.7', 'reason' => 'n=40 analogs', 'created_at' => now(), 'updated_at' => now(),
    ]);
    $thread = DB::table('warroom_threads')->where('env', 'demo')->where('kind', 'main')->value('id')
        ?? tap((string) Str::uuid(), fn (string $id) => DB::table('warroom_threads')->insert([
            'id' => $id, 'env' => 'demo', 'kind' => 'main', 'key' => 'main', 'title' => 'Main', 'created_at' => now(), 'updated_at' => now(),
        ]));
    WarroomMessage::query()->create([
        'env' => 'demo', 'thread_id' => $thread, 'event_id' => (string) Str::uuid(), 'author_type' => 'agent',
        'author' => 'devils_advocate', 'kind' => 'research_report', 'content_md' => 'Rating 2.',
        'card' => ['type' => 'da_review', 'proposal_id' => $p->id, 'risk_rating' => 2], 'mentions' => [],
    ]);

    $this->actingAs($this->user, 'web')
        ->getJson('/api/proposals?status=awaiting_owner&inst=btc-usdt-swap')
        ->assertOk()
        ->assertJsonCount(1, 'data')
        ->assertJsonPath('data.0.id', $p->id)
        ->assertJsonPath('data.0.risk_check.settings_version', 7)
        ->assertJsonPath('data.0.actions.approve', true);
    $this->actingAs($this->user, 'web')->getJson('/api/proposals?status=nope')->assertStatus(422);

    $this->actingAs($this->user, 'web')
        ->getJson("/api/proposals/{$p->id}")
        ->assertOk()
        ->assertJsonPath('data.votes.0.agent', 'historian')
        ->assertJsonPath('data.votes.0.confidence', '0.7')
        ->assertJsonPath('data.da_review.risk_rating', 2)
        ->assertJsonPath('data.risk_check.final_contracts', '3')
        ->assertJsonPath('data.entry', '4000')
        ->assertJsonCount(0, 'data.orders')
        ->assertJsonCount(1, 'data.versions');
});

it('asks the team with an owner message that mentions the Orchestrator', function (): void {
    Http::fake();
    $p = atl162Proposal();

    $res = $this->actingAs($this->user, 'web')
        ->postJson("/api/proposals/{$p->id}/ask-team", ['question' => 'Why not wait for CPI?'])
        ->assertCreated();

    $message = WarroomMessage::query()->findOrFail($res->json('message_id'));
    expect($message->author_type)->toBe('human')
        ->and($message->mentions)->toContain('orchestrator')
        ->and($message->content_md)->toContain('Why not wait for CPI?')
        ->and($message->card)->toMatchArray(['type' => 'proposal_question', 'proposal_id' => $p->id]);
    $warroom = atl162Events('atlas.warroom');
    expect($warroom)->toHaveCount(1)
        ->and($warroom[0]['id'])->toBe($message->event_id)
        ->and($warroom[0]['payload']['mentions'])->toContain('orchestrator')
        ->and(collect(atl162Events('atlas.proposals'))->pluck('type')->all())->toBe(['proposal.asked_team'])
        ->and(AuditLog::query()->where('action', 'proposal.asked_team')->exists())->toBeTrue();
    Http::assertNothingSent();
});
