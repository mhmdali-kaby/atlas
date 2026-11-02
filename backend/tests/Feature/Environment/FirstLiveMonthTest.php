<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Proposal;
use App\Models\User;
use App\Support\Settings\SettingsService;
use Illuminate\Http\Client\Request as HttpRequest;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\Str;

/*
 * ATL-238, decision #255 (6)(7): the first live month (30 days from system_state.live_started_at)
 * refuses live autonomy above 2 and clearing the live capital cap; approving a live proposal needs
 * live_confirmed: true. The Risk Engine side is tested in pytest; Telegram in TelegramTest.
 */

const FLM_CORE = 'http://127.0.0.1:8100';
const FLM_SECRET = 'atl238-test-only-internal-secret';

function flmLiveStarted(int $daysAgo): void
{
    DB::table('system_state')->updateOrInsert(['id' => 1], [
        'active_env' => 'live',
        'live_started_at' => Carbon::now()->subDays($daysAgo),
        'switched_at' => Carbon::now()->subDays($daysAgo),
        'preflight_attestations' => '{}',
        'created_at' => Carbon::now(),
        'updated_at' => Carbon::now(),
    ]);
}

/** The live settings row's version after setting a cap, so the month rules have something to keep. */
function flmLiveWithCap(): int
{
    $settings = app(SettingsService::class);
    $row = $settings->update('live', $settings->current('live')->version, ['bot_capital_cap_usdt' => '5000'], User::query()->firstOrFail());

    return $row->version;
}

function flmLiveProposal(): Proposal
{
    $p = Proposal::query()->create([
        'env' => 'live', 'version' => 1, 'kind' => 'entry', 'author' => 'strategist',
        'inst_id' => 'BTC-USDT-SWAP', 'side' => 'buy', 'order_type' => 'limit',
        'entry' => '4000', 'stop' => '3960', 'take_profits' => [['price' => '4100', 'pct' => '100']],
        'size_suggestion_pct' => '5', 'leverage_suggestion' => 3, 'expected_r' => '2.5', 'probability' => '0.5',
        'rationale_md' => 'Breakout retest.', 'invalidation' => '1H close below 3960.',
        'status' => 'awaiting_owner', 'approval_expires_at' => Carbon::now()->addMinutes(10),
    ]);
    DB::table('risk_checks')->insert([
        'id' => (string) Str::uuid(), 'env' => 'live', 'proposal_id' => $p->id,
        'settings_version' => app(SettingsService::class)->current('live')->version,
        'result' => 'approved', 'checks' => '[]', 'final_size' => '3', 'final_notional_usd' => '300',
        'leverage' => 2, 'reason' => 'test', 'decision' => 'awaiting_owner', 'created_at' => now(), 'updated_at' => now(),
    ]);

    return $p;
}

beforeEach(function (): void {
    config(['services.trading_core.url' => FLM_CORE, 'services.trading_core.secret' => FLM_SECRET]);
    $this->user = User::factory()->create();
    RateLimiter::clear('proposals-write|'.$this->user->id);
    $this->actingAs($this->user, 'web');
});

// --- AC1: settings ----------------------------------------------------------------------------

it('refuses live autonomy 3 ten days into the first live month, in English and Arabic', function (): void {
    flmLiveStarted(10);
    $version = flmLiveWithCap();

    $this->patchJson('/api/settings', ['env' => 'live', 'version' => $version, 'changes' => ['autonomy_level' => 3]])
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['changes.autonomy_level'])
        ->assertJsonPath('errors', fn (array $errors): bool => str_contains($errors['changes.autonomy_level'][0], 'first live month')
            && str_contains($errors['changes.autonomy_level'][0], 'level 2'));

    $ar = $this->withHeaders(['Accept-Language' => 'ar'])
        ->patchJson('/api/settings', ['env' => 'live', 'version' => $version, 'changes' => ['autonomy_level' => 4]])
        ->assertUnprocessable();
    expect($ar->json('errors')['changes.autonomy_level'][0])->toContain('الشهر الأول');

    $this->patchJson('/api/settings', ['env' => 'live', 'version' => $version, 'changes' => ['autonomy_level' => 2]])->assertOk();
    expect(DB::table('settings')->where('env', 'live')->value('autonomy_level'))->toBe(2);
});

it('accepts live autonomy 3 thirty-one days after live_started_at', function (): void {
    flmLiveStarted(31);
    $version = flmLiveWithCap();

    $this->patchJson('/api/settings', ['env' => 'live', 'version' => $version, 'changes' => ['autonomy_level' => 3]])
        ->assertOk()
        ->assertJsonPath('data.settings.autonomy_level', 3)
        ->assertJsonPath('data.first_live_month_ends_at', null);
});

it('refuses clearing the live capital cap inside the first month but allows changing it', function (): void {
    flmLiveStarted(10);
    $version = flmLiveWithCap();

    $this->patchJson('/api/settings', ['env' => 'live', 'version' => $version, 'changes' => ['bot_capital_cap_usdt' => null]])
        ->assertUnprocessable()
        ->assertJsonPath('errors', fn (array $errors): bool => str_contains($errors['changes.bot_capital_cap_usdt'][0], 'must stay set'));
    $this->withHeaders(['Accept-Language' => 'ar'])
        ->patchJson('/api/settings', ['env' => 'live', 'version' => $version, 'changes' => ['bot_capital_cap_usdt' => null]])
        ->assertUnprocessable()
        ->assertJsonPath('errors', fn (array $errors): bool => str_contains($errors['changes.bot_capital_cap_usdt'][0], 'رأس مال'));
    expect(app(SettingsService::class)->values(app(SettingsService::class)->current('live'))['bot_capital_cap_usdt'])->toBe('5000');

    $this->withHeaders(['Accept-Language' => 'en'])->patchJson('/api/settings', ['env' => 'live', 'version' => $version, 'changes' => ['bot_capital_cap_usdt' => '2500']])
        ->assertOk()
        ->assertJsonPath('data.settings.bot_capital_cap_usdt', '2500');
});

it('leaves demo and live-before-any-switch unchanged', function (): void {
    flmLiveStarted(10);
    $this->getJson('/api/settings?env=demo')->assertOk()->assertJsonPath('data.first_live_month_ends_at', null);
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['autonomy_level' => 4]])->assertOk();

    DB::table('system_state')->delete();
    $this->patchJson('/api/settings', ['env' => 'live', 'version' => app(SettingsService::class)->current('live')->version, 'changes' => [
        'autonomy_level' => 4,
        'bot_capital_cap_usdt' => null,
    ]])->assertOk();
});

it('exposes first_live_month_ends_at on the live settings inside the month', function (): void {
    flmLiveStarted(10);
    $ends = Carbon::now()->subDays(10)->addDays(30);

    $at = $this->getJson('/api/settings?env=live')->assertOk()->json('data.first_live_month_ends_at');
    expect(Carbon::parse($at)->diffInSeconds($ends, true))->toBeLessThan(2.0)
        ->and($this->getJson('/api/settings?env=live')->json('data.max_live_autonomy_level'))->toBe(2);
});

// --- AC3: approve -----------------------------------------------------------------------------

it('refuses to approve a live proposal without live_confirmed and sends nothing', function (mixed $confirmed): void {
    flmLiveStarted(10);
    flmLiveWithCap();
    Http::fake();
    $p = flmLiveProposal();

    $body = $confirmed === 'absent' ? [] : ['live_confirmed' => $confirmed];
    $this->postJson("/api/proposals/{$p->id}/approve", $body)
        ->assertUnprocessable()
        ->assertJsonPath('ok', false)
        ->assertJsonPath('error', 'live_confirmation_required')
        ->assertJsonPath('message', __('proposals.live_confirmation_required'));

    Http::assertNothingSent();
    expect($p->fresh()?->status)->toBe('awaiting_owner')
        ->and(AuditLog::query()->where('action', 'proposal.approved')->exists())->toBeFalse();
})->with(['absent', false, 'true', 1]);

it('approves a live proposal with live_confirmed: true through the execute call', function (): void {
    flmLiveStarted(10);
    flmLiveWithCap();
    $p = flmLiveProposal();
    $path = "/internal/proposals/{$p->id}/execute";
    Http::fake([FLM_CORE.$path => Http::response(['ok' => true, 'status' => 'sent', 'reason' => 'sent', 'order_id' => 'o-1'])]);

    $this->postJson("/api/proposals/{$p->id}/approve", ['live_confirmed' => true])
        ->assertOk()
        ->assertJsonPath('ok', true)
        ->assertJsonPath('execution.order_id', 'o-1');

    Http::assertSentCount(1);
    Http::assertSent(fn (HttpRequest $r): bool => $r->url() === FLM_CORE.$path);
    expect(AuditLog::query()->where('action', 'proposal.approved')->sole()->after['actor'])->toBe('owner');
});

it('does not ask a demo proposal for live_confirmed', function (): void {
    $settings = app(SettingsService::class);
    $p = Proposal::query()->create([
        'env' => 'demo', 'version' => 1, 'kind' => 'entry', 'author' => 'strategist',
        'inst_id' => 'BTC-USDT-SWAP', 'side' => 'buy', 'order_type' => 'limit',
        'entry' => '4000', 'stop' => '3960', 'take_profits' => [['price' => '4100', 'pct' => '100']],
        'size_suggestion_pct' => '5', 'leverage_suggestion' => 3, 'expected_r' => '2.5', 'probability' => '0.5',
        'rationale_md' => 'x', 'invalidation' => 'y', 'status' => 'awaiting_owner', 'approval_expires_at' => Carbon::now()->addMinutes(10),
    ]);
    DB::table('risk_checks')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'proposal_id' => $p->id,
        'settings_version' => $settings->current('demo')->version,
        'result' => 'approved', 'checks' => '[]', 'final_size' => '3', 'final_notional_usd' => '300',
        'leverage' => 2, 'reason' => 'test', 'decision' => 'awaiting_owner', 'created_at' => now(), 'updated_at' => now(),
    ]);
    Http::fake([FLM_CORE.'/*' => Http::response(['ok' => true, 'status' => 'sent', 'reason' => 'sent'])]);

    $this->postJson("/api/proposals/{$p->id}/approve")->assertOk()->assertJsonPath('ok', true);
    Http::assertSentCount(1);
});
