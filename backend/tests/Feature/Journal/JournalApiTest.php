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
        'tags' => PostgresTextArray::serialize($tags), 'data' => '{}',
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
        jeEntry('2026-10-20 12:00:00', ['breakout'], ['agent' => 'strategist']);
        jeEntry('2026-10-20 12:00:00', ['breakout'], ['inst_id' => 'XAU-USDT-SWAP']);
        jeEntry('2026-10-20 12:00:00', ['breakout'], ['env' => 'live']);
    });

    it('returns only matching rows newest first, across pages', function (): void {
        $query = ['tag' => ['breakout', 'pullback_to_support'], 'agent' => 'portfolio_manager',
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
        expect($res->getContent())->toContain('"data":{}');
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
