<?php

declare(strict_types=1);

use App\Casts\PostgresTextArray;
use App\Models\User;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

// ATL-200: GET /api/journal, /api/journal/{id}, /api/agents/scores and /api/agents/notebook.

/**
 * @param  list<string>  $tags
 * @param  array<string, mixed>  $overrides
 */
function jeEntry(string $createdAtUtc, array $tags = [], array $overrides = []): string
{
    $id = (string) Str::uuid();
    $ts = CarbonImmutable::parse($createdAtUtc, 'UTC')->format('Y-m-d H:i:s.uP');
    DB::table('journal_entries')->insert(array_merge([
        'id' => $id, 'env' => 'demo', 'agent' => 'portfolio_manager', 'inst_id' => 'BTC-USDT-SWAP',
        'proposal_id' => null, 'title' => 'Post-mortem '.$id, 'expected_md' => 'TP1 first',
        'actual_md' => 'Stopped out', 'lessons_md' => 'Wait for the retest.',
        'tags' => PostgresTextArray::serialize($tags), 'data' => '{"agents":["strategist","historian"]}',
        'created_at' => $ts, 'updated_at' => $ts,
    ], $overrides));

    return $id;
}

/**
 * @param  array<string, mixed>  $data
 */
function jeScore(string $agent, string $day, int $n, ?string $hitRate, ?string $brier, array $data = [], string $env = 'demo'): void
{
    DB::table('agent_scores')->insert([
        'id' => (string) Str::uuid(), 'agent' => $agent, 'env' => $env, 'day' => $day, 'n' => $n,
        'hit_rate' => $hitRate, 'brier_score' => $brier, 'data' => json_encode((object) $data),
        'created_at' => now(), 'updated_at' => now(),
    ]);
}

/**
 * @param  array<string, mixed>  $overrides
 */
function jeClosed(string $closedAtUtc, string $realised, array $overrides = []): void
{
    DB::table('positions_closed')->insert(array_merge([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'pos_id' => (string) random_int(1, PHP_INT_MAX),
        'inst_id' => 'BTC-USDT-SWAP', 'inst_type' => 'SWAP', 'mgn_mode' => 'isolated', 'pos_side' => 'net',
        'side' => 'long', 'close_type' => '2', 'lever' => '5', 'ccy' => 'USDT', 'size' => '1',
        'entry_px' => '100', 'exit_px' => '100', 'realised_pnl' => $realised, 'pnl' => $realised,
        'fee' => '0', 'funding_fee' => '0', 'liq_penalty' => '0', 'pnl_ratio' => '0',
        'opened_at' => CarbonImmutable::parse($closedAtUtc, 'UTC')->subHours(2)->format('Y-m-d H:i:s.uP'),
        'closed_at' => CarbonImmutable::parse($closedAtUtc, 'UTC')->format('Y-m-d H:i:s.uP'),
        'origin' => 'external', 'created_at' => now(), 'updated_at' => now(),
    ], $overrides));
}

/**
 * @param  array<string, mixed>  $query
 * @return list<list<string>>
 */
function jePages(mixed $test, array $query, int $limit): array
{
    $pages = [];
    $cursor = null;
    do {
        $params = [...$query, 'limit' => $limit] + ($cursor === null ? [] : ['cursor' => $cursor]);
        $res = $test->getJson('/api/journal?'.http_build_query($params))->assertOk();
        $pages[] = array_column($res->json('data'), 'id');
        $cursor = $res->json('meta.next_cursor');
    } while ($cursor !== null && count($pages) < 20);

    return $pages;
}

beforeEach(function (): void {
    config(['atlas_defaults.env.default' => 'demo']);
    $this->actingAs(User::factory()->create(), 'web');
});

it('rejects unauthenticated requests to all four endpoints', function (): void {
    auth()->guard('web')->logout();
    $this->getJson('/api/journal')->assertUnauthorized();
    $this->getJson('/api/journal/'.Str::uuid())->assertUnauthorized();
    $this->getJson('/api/agents/scores?days=7')->assertUnauthorized();
    $this->getJson('/api/agents/notebook?date=2026-10-20')->assertUnauthorized();
    $this->getJson('/api/journal/weekly-reviews')->assertUnauthorized();
});

describe('journal filters', function (): void {
    beforeEach(function (): void {
        // Baghdad 2026-10-20 .. 2026-10-21 = UTC [2026-10-19 21:00, 2026-10-21 21:00).
        $this->match = [
            jeEntry('2026-10-21 20:59:59.999999', ['breakout', 'loss']),            // 23:59 on the 21st
            jeEntry('2026-10-20 10:00:00.250000', ['pullback_to_support', 'win']),  // any-of match
            jeEntry('2026-10-20 10:00:00.250000', ['breakout']),                     // same instant: id breaks the tie
            jeEntry('2026-10-20 10:00:00.250000', ['breakout', 'crypto']),
            jeEntry('2026-10-19 21:00:00', ['breakout']),                            // 00:00 on the 20th
        ];
        jeEntry('2026-10-21 21:00:00', ['breakout']);                     // 00:00 on the 22nd: outside
        jeEntry('2026-10-19 20:59:59.999999', ['breakout']);              // the 19th: outside
        jeEntry('2026-10-20 12:00:00', ['range_fade']);                   // no matching tag
        // Not named in data.agents (the column `agent` is not what the filter reads).
        jeEntry('2026-10-20 12:00:00', ['breakout'], ['data' => '{"agents":["strategist"]}']);
        jeEntry('2026-10-20 12:00:00', ['breakout'], ['inst_id' => 'XAU-USDT-SWAP']);
        jeEntry('2026-10-20 12:00:00', ['breakout'], ['env' => 'live']);
    });

    it('returns only matching rows newest first, across pages', function (): void {
        $query = ['tag' => ['breakout', 'pullback_to_support'], 'agent' => 'historian',
            'inst_id' => 'BTC-USDT-SWAP', 'from' => '2026-10-20', 'to' => '2026-10-21'];

        $all = $this->getJson('/api/journal?'.http_build_query($query))->assertOk();
        $ids = array_column($all->json('data'), 'id');
        expect($ids)->toHaveCount(5)
            ->and($ids[0])->toBe($this->match[0])
            ->and($ids[4])->toBe($this->match[4])
            ->and(array_slice($ids, 1, 3))->toEqualCanonicalizing(array_slice($this->match, 1, 3))
            ->and($all->json('meta.next_cursor'))->toBeNull()
            ->and($all->json('meta.env'))->toBe('demo');

        $created = array_column($all->json('data'), 'created_at');
        $sorted = $created;
        rsort($sorted);
        expect($created)->toBe($sorted);

        // The tie at 10:00:00.250000 is broken by id descending.
        $tied = array_slice($ids, 1, 3);
        $tiedSorted = $tied;
        rsort($tiedSorted);
        expect($tied)->toBe($tiedSorted);

        $pages = jePages($this, $query, 2);
        expect($pages)->toHaveCount(3)
            ->and(array_merge(...$pages))->toBe($ids);
    });

    it('returns the full resource shape', function (): void {
        $res = $this->getJson('/api/journal?tag=loss')->assertOk();
        expect($res->json('data'))->toHaveCount(1)
            ->and(array_keys($res->json('data.0')))->toEqualCanonicalizing([
                'id', 'env', 'title', 'inst_id', 'agent', 'proposal_id', 'expected_md', 'actual_md',
                'lessons_md', 'tags', 'data', 'created_at',
            ])
            ->and($res->json('data.0.tags'))->toBe(['breakout', 'loss'])
            ->and($res->json('data.0.created_at'))->toBe('2026-10-21T20:59:59.999999Z');
        expect($res->json('data.0.data'))->toBe(['agents' => ['strategist', 'historian']]);

        $empty = jeEntry('2026-10-25 08:00:00', ['empty_data'], ['data' => '{}']);
        expect($this->getJson('/api/journal?tag=empty_data')->assertOk()->getContent())
            ->toContain('"id":"'.$empty.'"')->toContain('"data":{}');
    });

    it('filters agent on data.agents, not on the journal_entries.agent column', function (): void {
        $named = jeEntry('2026-10-26 08:00:00', ['agent_case'], ['agent' => 'portfolio_manager']);
        $pmListed = jeEntry('2026-10-26 07:00:00', ['agent_case'],
            ['agent' => 'portfolio_manager', 'data' => '{"agents":["portfolio_manager","devils_advocate"]}']);
        jeEntry('2026-10-26 06:00:00', ['agent_case'], ['agent' => 'historian', 'data' => '{"agents":["strategist"]}']);
        jeEntry('2026-10-26 05:00:00', ['agent_case'], ['data' => '{}']);
        jeEntry('2026-10-26 04:00:00', ['agent_case'], ['data' => '{"agents":"historian"}']);

        $ids = fn (string $agent): array => array_column(
            $this->getJson('/api/journal?tag=agent_case&agent='.$agent)->assertOk()->json('data'), 'id');

        expect($ids('historian'))->toBe([$named])
            ->and($ids('portfolio_manager'))->toBe([$pmListed])
            ->and($ids('devils_advocate'))->toBe([$pmListed])
            ->and($ids('market_scout'))->toBe([]);
    });

    it('filters by outcome from data.outcome or a bare tag', function (): void {
        $fromData = jeEntry('2026-10-25 08:00:00', ['breakout'], ['data' => json_encode(['outcome' => 'scratch'])]);
        $fromTag = jeEntry('2026-10-25 07:00:00', ['scratch']);

        $ids = array_column($this->getJson('/api/journal?outcome=scratch')->assertOk()->json('data'), 'id');
        expect($ids)->toBe([$fromData, $fromTag]);
        $this->getJson('/api/journal?outcome=draw')->assertUnprocessable()->assertJsonValidationErrors('outcome');
    });

    it('validates its query parameters', function (): void {
        $this->getJson('/api/journal?from=2026-10-22&to=2026-10-21')->assertUnprocessable()->assertJsonValidationErrors('to');
        $this->getJson('/api/journal?from=20-10-2026')->assertUnprocessable()->assertJsonValidationErrors('from');
        $this->getJson('/api/journal?limit=0')->assertUnprocessable()->assertJsonValidationErrors('limit');
        $this->getJson('/api/journal?cursor=bogus')->assertUnprocessable()->assertJsonValidationErrors('cursor');
        $this->getJson('/api/journal?agent=x%27%3B')->assertUnprocessable()->assertJsonValidationErrors('agent');
    });
});

describe('environment isolation', function (): void {
    it('never returns a live entry while demo is active', function (): void {
        $live = jeEntry('2026-10-20 12:00:00', ['breakout'], ['env' => 'live']);
        $demo = jeEntry('2026-10-20 11:00:00', ['breakout']);

        $ids = array_column($this->getJson('/api/journal')->assertOk()->json('data'), 'id');
        expect($ids)->toBe([$demo]);

        $this->getJson('/api/journal/'.$live)->assertNotFound();
        $this->getJson('/api/journal/'.$demo)->assertOk()->assertJsonPath('data.id', $demo);
        $this->getJson('/api/journal/'.Str::uuid())->assertNotFound();
    });
});

describe('agent scores', function (): void {
    beforeEach(function (): void {
        CarbonImmutable::setTestNow(CarbonImmutable::parse('2026-10-28 10:00:00', 'UTC')); // 13:00 Baghdad
    });

    afterEach(function (): void {
        CarbonImmutable::setTestNow();
    });

    it('returns each agent\'s rows in the window and an n-weighted hit rate', function (): void {
        // days=7 → Baghdad 2026-10-22 .. 2026-10-28.
        jeScore('market_scout', '2026-10-21', 50, '0.9000', '0.100000');                       // outside
        jeScore('market_scout', '2026-10-22', 4, '0.2500', '0.300000', ['selection_edge' => '0.4']);
        jeScore('market_scout', '2026-10-25', 6, '0.5000', '0.200000', ['reliability_weight' => '1.2']);
        jeScore('market_scout', '2026-10-27', 10, '0.8000', null, ['reliability_weight' => '0.9']);
        jeScore('market_scout', '2026-10-28', 0, null, null);
        jeScore('market_scout', '2026-10-29', 9, '1.0000', '0.000000');                       // future: outside
        jeScore('news_intelligence', '2026-10-26', 3, '1.0000', '0.050000');
        jeScore('news_intelligence', '2026-10-26', 3, '0.0000', '0.050000', [], 'live');      // live: never shown

        $res = $this->getJson('/api/agents/scores?days=7')->assertOk();
        expect($res->json('meta'))->toMatchArray(['env' => 'demo', 'days' => 7, 'from' => '2026-10-22', 'to' => '2026-10-28']);

        $agents = collect($res->json('data'))->keyBy('agent');
        $scout = $agents['market_scout'];
        expect(array_column($scout['days'], 'day'))->toBe(['2026-10-22', '2026-10-25', '2026-10-27', '2026-10-28'])
            ->and($scout['days'][0])->toBe(['day' => '2026-10-22', 'n' => 4, 'hit_rate' => '0.2500', 'brier_score' => '0.300000', 'data' => ['selection_edge' => '0.4']])
            // (0.25×4 + 0.5×6 + 0.8×10) / 20 = 12 / 20 = 0.6
            ->and($scout['summary'])->toBe([
                'n' => 20, 'days_scored' => 4, 'hit_rate' => '0.6000', 'brier_score' => '0.250000',
                'reliability_weight' => '0.9', 'reliability_weight_day' => '2026-10-27',
            ]);

        $news = $agents['news_intelligence'];
        expect($news['days'])->toHaveCount(1)
            ->and($news['summary']['hit_rate'])->toBe('1.0000')
            ->and($news['summary']['reliability_weight'])->toBeNull();

        // Agents without rows are listed with an empty window.
        expect($agents['historian']['days'])->toBe([])
            ->and($agents['historian']['summary']['hit_rate'])->toBeNull();
        expect($res->getContent())->toContain('"data":{}');
    });

    it('sums pnl_attributed_usd from the same closed trades as Performance\'s By-agent breakdown, over the window', function (): void {
        // days=7 → Baghdad 2026-10-22 .. 2026-10-28, i.e. UTC [2026-10-21 21:00, 2026-10-28 21:00).
        jeClosed('2026-10-21 20:00:00', '100.00', ['agent' => 'portfolio_manager']);               // outside: Baghdad 2026-10-21 23:00
        jeClosed('2026-10-22 06:00:00', '12.50', ['agent' => 'portfolio_manager']);
        jeClosed('2026-10-24 06:00:00', '-30.00', ['agent' => 'portfolio_manager']);
        jeClosed('2026-10-25 06:00:00', '5.00', ['agent' => 'portfolio_manager', 'ccy' => 'BTC']);  // not USD-stable: excluded
        jeClosed('2026-10-26 06:00:00', '9.00');                                                    // no agent (external): excluded

        $res = $this->getJson('/api/agents/scores?days=7')->assertOk();
        $agents = collect($res->json('data'))->keyBy('agent');
        expect($agents['portfolio_manager']['pnl_attributed_usd'])->toBe('-17.5')
            ->and($agents['market_scout']['pnl_attributed_usd'])->toBeNull();
    });

    it('defaults to 30 days and accepts only 7, 30 and 90', function (): void {
        $this->getJson('/api/agents/scores')->assertOk()->assertJsonPath('meta.days', 30)->assertJsonPath('meta.from', '2026-09-29');
        $this->getJson('/api/agents/scores?days=90')->assertOk()->assertJsonPath('meta.from', '2026-07-31');
        $this->getJson('/api/agents/scores?days=5')->assertUnprocessable()->assertJsonValidationErrors('days');
        $this->getJson('/api/agents/scores?days=abc')->assertUnprocessable()->assertJsonValidationErrors('days');
    });
});

describe('notebook', function (): void {
    it('returns the active env\'s notes of one Baghdad day in order', function (): void {
        $insert = static function (string $day, string $agent, string $at, string $env = 'demo'): string {
            $id = (string) Str::uuid();
            DB::table('agent_notebook')->insert([
                'id' => $id, 'env' => $env, 'day' => $day, 'agent' => $agent, 'note_md' => "note by {$agent}",
                'data' => '{}', 'created_at' => $at, 'updated_at' => $at,
            ]);

            return $id;
        };
        $second = $insert('2026-10-20', 'strategist', '2026-10-20 09:00:00+00');
        $first = $insert('2026-10-20', 'historian', '2026-10-20 05:00:00+00');
        $insert('2026-10-21', 'historian', '2026-10-20 22:00:00+00');
        $insert('2026-10-20', 'historian', '2026-10-20 06:00:00+00', 'live');

        $res = $this->getJson('/api/agents/notebook?date=2026-10-20')->assertOk();
        expect(array_column($res->json('data'), 'id'))->toBe([$first, $second])
            ->and($res->json('data.0'))->toMatchArray(['agent' => 'historian', 'note_md' => 'note by historian', 'created_at' => '2026-10-20T05:00:00.000000Z'])
            ->and($res->json('meta'))->toMatchArray(['env' => 'demo', 'date' => '2026-10-20']);

        $this->getJson('/api/agents/notebook?date=2026/10/20')->assertUnprocessable()->assertJsonValidationErrors('date');
    });
});

/**
 * @param  array<string, mixed>  $data
 */
function jeReview(string $createdAtUtc, array $data = [], string $envName = 'demo', string $kind = 'weekly_review'): string
{
    $id = (string) Str::uuid();
    $ts = CarbonImmutable::parse($createdAtUtc, 'UTC')->format('Y-m-d H:i:s.uP');
    DB::table('research_reports')->insert([
        'id' => $id, 'env' => $envName, 'agent' => 'historian', 'inst_id' => null, 'kind' => $kind,
        'content_md' => "Review {$id}", 'data' => json_encode((object) $data),
        'created_at' => $ts, 'updated_at' => $ts,
    ]);

    return $id;
}

describe('weekly reviews', function (): void {
    it('lists the active env\'s weekly reviews newest first with a working cursor', function (): void {
        // Sunday 2026-10-25 18:00 Baghdad = 15:00 UTC.
        $oct25 = jeReview('2026-10-25 15:00:00', ['week_start' => '2026-10-19', 'setups' => [['name' => 'breakout', 'r' => '1.4']]]);
        $tieA = jeReview('2026-10-18 15:00:00.500000');
        $tieB = jeReview('2026-10-18 15:00:00.500000');
        $oct11 = jeReview('2026-10-11 15:00:00', ['week_start' => 'not-a-date']);
        jeReview('2026-10-26 15:00:00', [], 'live');
        jeReview('2026-10-26 15:00:00', [], 'demo', 'regime_table');

        $res = $this->getJson('/api/journal/weekly-reviews')->assertOk();
        $ids = array_column($res->json('data'), 'id');
        $tied = [$tieA, $tieB];
        rsort($tied);
        expect($ids)->toBe([$oct25, ...$tied, $oct11])
            ->and($res->json('meta.env'))->toBe('demo')
            ->and($res->json('meta.next_cursor'))->toBeNull()
            ->and(array_keys($res->json('data.0')))->toEqualCanonicalizing([
                'id', 'env', 'agent', 'week_start', 'week_end', 'content_md', 'data', 'created_at',
            ])
            ->and($res->json('data.0'))->toMatchArray([
                'week_start' => '2026-10-19', 'week_end' => '2026-10-25', 'agent' => 'historian',
                'content_md' => "Review {$oct25}", 'created_at' => '2026-10-25T15:00:00.000000Z',
            ])
            ->and($res->json('data.0.data'))->toEqual(['week_start' => '2026-10-19', 'setups' => [['name' => 'breakout', 'r' => '1.4']]])
            // No usable data.week_start: the Monday of the Baghdad week it was written in.
            ->and($res->json('data.1.week_start'))->toBe('2026-10-12')
            ->and($res->json('data.3.week_start'))->toBe('2026-10-05');
        expect($res->getContent())->toContain('"data":{}');

        $pages = [];
        $cursor = null;
        do {
            $params = ['limit' => 3] + ($cursor === null ? [] : ['cursor' => $cursor]);
            $page = $this->getJson('/api/journal/weekly-reviews?'.http_build_query($params))->assertOk();
            $pages[] = array_column($page->json('data'), 'id');
            $cursor = $page->json('meta.next_cursor');
        } while ($cursor !== null && count($pages) < 10);
        expect($pages)->toHaveCount(2)->and(array_merge(...$pages))->toBe($ids);
    });

    it('validates its cursor and limit', function (): void {
        $this->getJson('/api/journal/weekly-reviews?cursor=bogus')->assertUnprocessable()->assertJsonValidationErrors('cursor');
        $this->getJson('/api/journal/weekly-reviews?limit=0')->assertUnprocessable()->assertJsonValidationErrors('limit');
    });
});
