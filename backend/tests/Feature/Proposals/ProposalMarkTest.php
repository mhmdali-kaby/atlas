<?php

declare(strict_types=1);

use App\Models\Proposal;
use App\Models\User;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Str;

/*
 * ATL-188: /api/proposals carries the live mark (and when trading_core received it) from the
 * market snapshot hash of the proposal's instrument; null for both when there is none.
 */

function atl188Proposal(string $instId = 'SOL-USDT-SWAP'): Proposal
{
    return Proposal::query()->create([
        'env' => 'demo', 'version' => 1, 'kind' => 'entry', 'author' => 'strategist',
        'inst_id' => $instId, 'side' => 'buy', 'order_type' => 'limit', 'entry' => '249.7', 'stop' => '244.1',
        'take_profits' => [['price' => '258.9', 'pct' => '100']], 'size_suggestion_pct' => '5',
        'leverage_suggestion' => 3, 'expected_r' => '2.5', 'probability' => '0.5',
        'rationale_md' => 'x', 'invalidation' => 'y', 'status' => 'awaiting_owner',
        'approval_expires_at' => Carbon::now()->addMinutes(10),
    ]);
}

beforeEach(function (): void {
    $this->user = User::factory()->create();
    // Keys of this test only, so parallel checkouts sharing the Redis test DB cannot collide.
    $this->prefix = 'atl188:'.Str::lower(Str::random(10)).':md';
    config(['atlas_defaults.market_ws.key_prefix' => $this->prefix]);
});

afterEach(function (): void {
    Redis::connection('events')->command('del', ["{$this->prefix}:demo:SOL-USDT-SWAP"]);
});

it('returns mark and mark_at from the md hash on show, list and the mark endpoint', function (): void {
    $p = atl188Proposal();
    $receivedMs = 1_793_520_000_123; // 2026-11-01T08:00:00.123Z
    Redis::connection('events')->command('hset', ["{$this->prefix}:demo:SOL-USDT-SWAP",
        'mark', json_encode(['instType' => 'SWAP', 'instId' => 'SOL-USDT-SWAP', 'markPx' => '249.95', 'ts' => '1793519999000']),
        'mark-price_ms', (string) $receivedMs,
        'last_msg_ms', (string) $receivedMs,
    ]);

    $this->actingAs($this->user, 'web')->getJson("/api/proposals/{$p->id}")->assertOk()
        ->assertJsonPath('data.mark', '249.95')
        ->assertJsonPath('data.mark_at', '2026-11-01T08:00:00.123Z');
    $this->actingAs($this->user, 'web')->getJson('/api/proposals')->assertOk()
        ->assertJsonPath('data.0.mark', '249.95')
        ->assertJsonPath('data.0.mark_at', '2026-11-01T08:00:00.123Z');
    $this->actingAs($this->user, 'web')->getJson("/api/proposals/{$p->id}/mark")->assertOk()
        ->assertExactJson(['data' => ['inst_id' => 'SOL-USDT-SWAP', 'mark' => '249.95', 'mark_at' => '2026-11-01T08:00:00.123Z']]);
});

it('returns null mark and mark_at when the hash has no entry', function (): void {
    $p = atl188Proposal();

    $this->actingAs($this->user, 'web')->getJson("/api/proposals/{$p->id}")->assertOk()
        ->assertJsonPath('data.mark', null)
        ->assertJsonPath('data.mark_at', null);
    $this->actingAs($this->user, 'web')->getJson("/api/proposals/{$p->id}/mark")->assertOk()
        ->assertJsonPath('data.mark', null)
        ->assertJsonPath('data.mark_at', null);
});

it('returns null when the hash has a ticker but no mark push yet', function (): void {
    $p = atl188Proposal();
    Redis::connection('events')->command('hset', ["{$this->prefix}:demo:SOL-USDT-SWAP", 'last_msg_ms', '1793520000000', 'ticker', '{"last":"250"}']);

    $this->actingAs($this->user, 'web')->getJson("/api/proposals/{$p->id}")->assertOk()
        ->assertJsonPath('data.mark', null)
        ->assertJsonPath('data.mark_at', null);
});

it('requires a session for the mark endpoint', function (): void {
    $p = atl188Proposal();
    $this->getJson("/api/proposals/{$p->id}/mark")->assertUnauthorized();
});
