<?php

declare(strict_types=1);

use Illuminate\Database\QueryException;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Str;

/** @param array<string, mixed> $overrides */
function insertCycle(array $overrides = []): string
{
    $id = (string) Str::uuid();
    DB::table('cycles')->insert(array_merge([
        'id' => $id, 'env' => 'demo', 'kind' => 'scheduled', 'started_at' => '2026-10-30T05:10:00Z',
    ], $overrides));

    return $id;
}

/** @param array<string, mixed> $overrides */
function insertProposal(array $overrides = []): string
{
    $id = (string) Str::uuid();
    DB::table('proposals')->insert(array_merge([
        'id' => $id, 'env' => 'demo', 'author' => 'strategist', 'inst_id' => 'BTC-USDT-SWAP',
        'side' => 'buy', 'order_type' => 'limit', 'entry' => '68000.5', 'stop' => '66900',
        'take_profits' => json_encode([['price' => '70500', 'pct' => '50']]),
        'size_suggestion_pct' => '2.5', 'leverage_suggestion' => 3, 'expected_r' => '2.1',
        'probability' => '0.55',
    ], $overrides));

    return $id;
}

it('creates the four decision tables', function (): void {
    foreach (['cycles', 'proposals', 'votes', 'risk_checks'] as $table) {
        expect(Schema::hasTable($table))->toBeTrue();
    }
    expect(Schema::hasColumns('proposals', [
        'env', 'cycle_id', 'version', 'parent_id', 'kind', 'author', 'inst_id', 'side', 'order_type',
        'entry', 'entry_zone', 'stop', 'take_profits', 'time_stop_minutes', 'size_suggestion_pct',
        'leverage_suggestion', 'expected_r', 'probability', 'rationale_md', 'invalidation', 'evidence', 'status',
    ]))->toBeTrue()
        ->and(Schema::hasColumns('risk_checks', [
            'env', 'proposal_id', 'settings_version', 'result', 'checks', 'final_size', 'final_notional_usd',
            'leverage', 'reason', 'decision',
        ]))->toBeTrue()
        ->and(Schema::hasColumns('cycles', ['env', 'kind', 'state', 'started_at', 'deadlines', 'closed_at']))->toBeTrue()
        ->and(Schema::hasColumns('votes', ['env', 'proposal_id', 'agent', 'vote', 'confidence', 'reason']))->toBeTrue();
});

it('accepts every §8 proposal status and refuses anything else', function (string $status): void {
    $id = insertProposal(['status' => $status]);

    expect(DB::table('proposals')->where('id', $id)->value('status'))->toBe($status);
})->with([
    'draft', 'under_review', 'voting', 'passed', 'rejected', 'risk_checked', 'awaiting_owner',
    'sent', 'partially_filled', 'filled', 'closed', 'cancelled', 'expired',
]);

it('rejects a proposal status outside the §8 lifecycle', function (): void {
    insertProposal(['status' => 'approved_by_chat']);
})->throws(QueryException::class, 'proposals_status_check');

it('defaults a proposal to a draft entry at version 1', function (): void {
    $row = DB::table('proposals')->where('id', insertProposal())->first();

    expect($row->status)->toBe('draft')
        ->and($row->kind)->toBe('entry')
        ->and($row->version)->toBe(1)
        ->and($row->evidence)->toBe('[]');
});

it('refuses an entry proposal without a stop', function (): void {
    insertProposal(['stop' => null]);
})->throws(QueryException::class, 'proposals_entry_complete');

it('allows a close proposal with no side, order type or stop', function (): void {
    $id = insertProposal(['kind' => 'close', 'author' => 'portfolio_manager', 'side' => null, 'order_type' => null, 'stop' => null]);

    expect(DB::table('proposals')->where('id', $id)->value('kind'))->toBe('close');
});

it('refuses an unknown proposal kind', function (): void {
    insertProposal(['kind' => 'withdraw']);
})->throws(QueryException::class, 'proposals_kind_check');

it('links a revision to its parent and requires version 1 to have none', function (): void {
    $parent = insertProposal();
    $child = insertProposal(['version' => 2, 'parent_id' => $parent]);

    expect(DB::table('proposals')->where('id', $child)->value('parent_id'))->toBe($parent);

    expect(fn () => DB::transaction(fn () => insertProposal(['version' => 2])))
        ->toThrow(QueryException::class, 'proposals_parent_matches_version');
    expect(fn () => DB::transaction(fn () => insertProposal(['version' => 3, 'parent_id' => $parent])))
        ->toThrow(UniqueConstraintViolationException::class);
});

it('refuses a probability outside 0..1', function (): void {
    insertProposal(['probability' => '1.2']);
})->throws(QueryException::class, 'proposals_probability_range');

it('refuses a cycle state outside the §7.9 machine', function (): void {
    expect(DB::table('cycles')->where('id', insertCycle())->value('state'))->toBe('cycle_start');

    insertCycle(['state' => 'trading']);
})->throws(QueryException::class, 'cycles_state_check');

it('stores one vote per agent per proposal', function (): void {
    $proposal = insertProposal(['cycle_id' => insertCycle()]);
    $vote = ['env' => 'demo', 'proposal_id' => $proposal, 'agent' => 'historian', 'vote' => 'take', 'confidence' => '0.7'];
    DB::table('votes')->insert(['id' => (string) Str::uuid(), ...$vote]);

    expect(fn () => DB::transaction(fn () => DB::table('votes')->insert(['id' => (string) Str::uuid(), ...$vote])))
        ->toThrow(UniqueConstraintViolationException::class);
    expect(fn () => DB::transaction(fn () => DB::table('votes')->insert(['id' => (string) Str::uuid(), ...$vote, 'agent' => 'devils_advocate', 'vote' => 'maybe'])))
        ->toThrow(QueryException::class, 'votes_vote_check');
});

it('requires a decision on approved risk checks and none on rejected ones', function (): void {
    $proposal = insertProposal();
    $base = ['env' => 'demo', 'proposal_id' => $proposal, 'settings_version' => 3];

    DB::table('risk_checks')->insert(['id' => (string) Str::uuid(), ...$base, 'result' => 'approved',
        'final_size' => '0.12', 'final_notional_usd' => '8160.06', 'leverage' => 3, 'decision' => 'awaiting_owner']);
    DB::table('risk_checks')->insert(['id' => (string) Str::uuid(), ...$base, 'result' => 'rejected', 'reason' => 'stale data']);

    expect(DB::table('risk_checks')->where('proposal_id', $proposal)->count())->toBe(2);
    expect(fn () => DB::transaction(fn () => DB::table('risk_checks')->insert(['id' => (string) Str::uuid(), ...$base, 'result' => 'approved'])))
        ->toThrow(QueryException::class, 'risk_checks_decision_matches_result');
    expect(fn () => DB::transaction(fn () => DB::table('risk_checks')->insert(['id' => (string) Str::uuid(), ...$base, 'result' => 'maybe', 'decision' => 'send_now'])))
        ->toThrow(QueryException::class, 'risk_checks_result_check');
});
