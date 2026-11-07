<?php

declare(strict_types=1);

use App\Models\User;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

// ATL-172: GET /api/portfolio/history and GET /api/exports/{fills|closed}.csv.

function phTs(string $utc): string
{
    return CarbonImmutable::parse($utc, 'UTC')->format('Y-m-d H:i:s.uP');
}

function phInstrument(string $instId, string $assetClass): void
{
    DB::table('instruments')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'inst_id' => $instId, 'inst_type' => 'SWAP',
        'lot_sz' => '1', 'min_sz' => '1', 'tick_sz' => '0.1', 'state' => 'live', 'asset_class' => $assetClass,
        'created_at' => now(), 'updated_at' => now(),
    ]);
}

/**
 * @param  array<string, mixed>  $overrides
 */
function phClosed(string $closedAtUtc, string $side, string $realised, array $overrides = []): string
{
    $id = (string) Str::uuid();
    DB::table('positions_closed')->insert(array_merge([
        'id' => $id, 'env' => 'demo', 'pos_id' => (string) random_int(1, PHP_INT_MAX),
        'inst_id' => 'ETH-USDT-SWAP', 'inst_type' => 'SWAP', 'mgn_mode' => 'isolated', 'pos_side' => 'net',
        'side' => $side, 'close_type' => '2', 'lever' => '5', 'ccy' => 'USDT', 'size' => '2',
        'entry_px' => '3301.5', 'exit_px' => '3350.12', 'realised_pnl' => $realised, 'pnl' => $realised,
        'fee' => '-0.67', 'funding_fee' => '-0.012', 'liq_penalty' => '0', 'pnl_ratio' => '0.01',
        'opened_at' => phTs($closedAtUtc.' -2 hours'), 'closed_at' => phTs($closedAtUtc),
        'origin' => 'external', 'created_at' => now(), 'updated_at' => now(),
    ], $overrides));

    return $id;
}

function phFill(string $tsUtc, string $side, string $fillPnl, string $instId = 'ETH-USDT-SWAP'): string
{
    $id = (string) Str::uuid();
    DB::table('fills')->insert([
        'id' => $id, 'env' => 'demo', 'trade_id' => (string) random_int(1, PHP_INT_MAX),
        'ord_id' => (string) random_int(1, PHP_INT_MAX), 'inst_id' => $instId, 'side' => $side,
        'pos_side' => 'net', 'fill_px' => '3350.120000000000000000', 'fill_sz' => '2', 'fee' => '-0.67',
        'fee_ccy' => 'USDT', 'exec_type' => 'T', 'fill_pnl' => $fillPnl, 'ts' => phTs($tsUtc),
        'created_at' => now(), 'updated_at' => now(),
    ]);

    return $id;
}

function phProposal(string $entry, string $stop): string
{
    $id = (string) Str::uuid();
    DB::table('proposals')->insert([
        'id' => $id, 'env' => 'demo', 'author' => 'strategist', 'inst_id' => 'ETH-USDT-SWAP', 'side' => 'buy',
        'order_type' => 'limit', 'entry' => $entry, 'stop' => $stop, 'status' => 'closed',
        'created_at' => now(), 'updated_at' => now(),
    ]);

    return $id;
}

/**
 * Every page of a filtered list, following next_cursor.
 *
 * @param  array<string, string|int>  $query
 * @return list<list<string>> ids per page
 */
function phPages(mixed $test, array $query): array
{
    $pages = [];
    $cursor = null;
    do {
        $params = $cursor === null ? $query : [...$query, 'cursor' => $cursor];
        $res = $test->getJson('/api/portfolio/history?'.http_build_query($params))->assertOk();
        $pages[] = array_column($res->json('data'), 'id');
        $cursor = $res->json('meta.next_cursor');
    } while ($cursor !== null && count($pages) < 20);

    return $pages;
}

beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
});

it('requires authentication', function (): void {
    auth()->guard('web')->logout();
    $this->getJson('/api/portfolio/history')->assertUnauthorized();
    $this->getJson('/api/exports/closed.csv')->assertUnauthorized();
});

describe('closed positions filters (AC2)', function (): void {
    beforeEach(function (): void {
        // Baghdad 2026-10-20 .. 2026-10-21 = UTC [2026-10-19 21:00, 2026-10-21 21:00).
        $this->match = [
            phClosed('2026-10-21 20:30:00', 'short', '-4.5'),          // 23:30 Baghdad on the 21st
            phClosed('2026-10-20 10:00:00.250000', 'short', '-1'),
            phClosed('2026-10-20 10:00:00.250000', 'short', '-2'),     // same instant: id breaks the tie
            phClosed('2026-10-20 10:00:00.250000', 'short', '-3'),
            phClosed('2026-10-19 21:00:00', 'short', '-0.01'),         // 00:00 Baghdad on the 20th
        ];
        phClosed('2026-10-21 21:00:00', 'short', '-1');   // 00:00 Baghdad on the 22nd: outside
        phClosed('2026-10-19 20:59:59.999999', 'short', '-1');  // 23:59:59 Baghdad on the 19th: outside
        phClosed('2026-10-20 12:00:00', 'long', '-1');    // wrong side
        phClosed('2026-10-20 12:00:00', 'short', '5');    // a win
        phClosed('2026-10-20 12:00:00', 'short', '0');    // flat is neither win nor loss
        phClosed('2026-10-20 12:00:00', 'short', '-1', ['env' => 'live']);
    });

    it('returns only short losses closed on the Baghdad days, newest first', function (): void {
        $res = $this->getJson('/api/portfolio/history?type=closed&side=sell&win_loss=loss&from=2026-10-20&to=2026-10-21')
            ->assertOk();

        $ids = array_column($res->json('data'), 'id');
        expect($ids)->toHaveCount(5)
            ->and($ids[0])->toBe($this->match[0])
            ->and($ids[4])->toBe($this->match[4])
            ->and(collect($res->json('data'))->pluck('side')->unique()->all())->toBe(['short'])
            ->and(collect($res->json('data'))->pluck('outcome')->unique()->all())->toBe(['loss'])
            ->and($res->json('meta.next_cursor'))->toBeNull()
            ->and($res->json('data.0.closed_at'))->toBe('2026-10-21T20:30:00.000000Z')
            ->and($res->json('data.0.realised_pnl'))->toBe('-4.5');

        // side=short is the same filter as side=sell for closed positions.
        $short = $this->getJson('/api/portfolio/history?type=closed&side=short&win_loss=loss&from=2026-10-20&to=2026-10-21');
        expect(array_column($short->json('data'), 'id'))->toBe($ids);
    });

    it('pages with a stable cursor: every row once, the same pages every time', function (): void {
        $query = ['type' => 'closed', 'side' => 'sell', 'win_loss' => 'loss', 'from' => '2026-10-20', 'to' => '2026-10-21', 'limit' => 2];

        $pages = phPages($this, $query);
        expect(array_map(count(...), $pages))->toBe([2, 2, 1]);

        $all = array_merge(...$pages);
        $single = array_column($this->getJson('/api/portfolio/history?'.http_build_query([...$query, 'limit' => 50]))->json('data'), 'id');
        expect($all)->toBe($single)->and(array_unique($all))->toHaveCount(5);

        // Rows added after the first page was read do not shift the later pages.
        $first = $this->getJson('/api/portfolio/history?'.http_build_query($query));
        phClosed('2026-10-21 20:45:00', 'short', '-9');
        $second = $this->getJson('/api/portfolio/history?'.http_build_query([...$query, 'cursor' => $first->json('meta.next_cursor')]));
        expect(array_column($second->json('data'), 'id'))->toBe($pages[1])
            ->and(phPages($this, $query))->toBe(phPages($this, $query));
    });

    it('filters by instrument, asset class, agent and strategy tag', function (): void {
        phInstrument('XAU-USDT-SWAP', 'commodity');
        $gold = phClosed('2026-10-20 13:00:00', 'long', '7', [
            'inst_id' => 'XAU-USDT-SWAP', 'agent' => 'strategist', 'setup_tag' => 'breakout',
        ]);

        foreach (['inst_id=XAU-USDT-SWAP', 'asset_class=commodity', 'agent=strategist', 'strategy_tag=breakout'] as $filter) {
            $ids = array_column($this->getJson("/api/portfolio/history?type=closed&{$filter}")->assertOk()->json('data'), 'id');
            expect($ids)->toBe([$gold]);
        }
        $this->getJson('/api/portfolio/history?type=closed&asset_class=stock')->assertOk()->assertJsonCount(0, 'data');
    });
});

it('filters fills by side, win/loss and Baghdad days', function (): void {
    $match = phFill('2026-10-20 08:00:00', 'sell', '-3.2');
    phFill('2026-10-20 08:00:00', 'buy', '-3.2');
    phFill('2026-10-20 08:00:00', 'sell', '0');
    phFill('2026-10-20 08:00:00', 'sell', '4');
    phFill('2026-10-21 21:00:00', 'sell', '-1');

    $res = $this->getJson('/api/portfolio/history?type=fills&side=sell&win_loss=loss&from=2026-10-20&to=2026-10-21')
        ->assertOk();

    expect(array_column($res->json('data'), 'id'))->toBe([$match])
        ->and($res->json('data.0'))->toMatchArray([
            'side' => 'sell', 'price' => '3350.12', 'size' => '2', 'fee' => '-0.67', 'fill_pnl' => '-3.2',
            'ts' => '2026-10-20T08:00:00.000000Z', 'origin' => 'external', 'agent' => null,
        ]);
});

it('rejects filters that do not apply and malformed cursors with 422', function (): void {
    $this->getJson('/api/portfolio/history?type=fills&strategy_tag=breakout')->assertUnprocessable()->assertJsonValidationErrors('strategy_tag');
    $this->getJson('/api/portfolio/history?type=fills&side=long')->assertUnprocessable()->assertJsonValidationErrors('side');
    $this->getJson('/api/portfolio/history?type=closed&from=2026-10-22&to=2026-10-21')->assertUnprocessable()->assertJsonValidationErrors('to');
    $this->getJson('/api/portfolio/history?type=closed&from=20-10-2026')->assertUnprocessable()->assertJsonValidationErrors('from');
    $this->getJson('/api/portfolio/history?type=other')->assertUnprocessable()->assertJsonValidationErrors('type');

    $bad = base64_encode(json_encode(['closed_at' => 'tomorrow', 'id' => 'x', '_pointsToNextItems' => true], JSON_THROW_ON_ERROR));
    $this->getJson('/api/portfolio/history?type=closed&cursor='.urlencode($bad))->assertUnprocessable()->assertJsonValidationErrors('cursor');
    $this->get('/api/exports/orders.csv')->assertNotFound();
});

it('exports closed positions as CSV with a header, one row per filtered position and plain decimals (AC3)', function (): void {
    $kept = phClosed('2026-10-20 10:00:00.123456', 'short', '-12.340000000000000000', [
        'pos_id' => '1777', 'entry_px' => '0.300000000000000000', 'exit_px' => '0.1', 'size' => '1000000',
        'fee' => '-0.000000010000000000', 'funding_fee' => '0', 'liq_penalty' => null, 'agent' => '=HYPERLINK("x")',
    ]);
    phClosed('2026-10-20 11:00:00', 'short', '1.5');   // a win: filtered out
    phClosed('2026-10-20 12:00:00', 'long', '-1');     // wrong side: filtered out

    $res = $this->get('/api/exports/closed.csv?side=sell&win_loss=loss&from=2026-10-20&to=2026-10-20');
    $res->assertOk();
    expect($res->headers->get('Content-Type'))->toStartWith('text/csv')
        ->and($res->headers->get('Content-Disposition'))->toContain('attachment')->toContain('.csv');

    $lines = array_values(array_filter(explode("\n", $res->streamedContent()), static fn (string $l): bool => $l !== ''));
    expect($lines)->toHaveCount(2)
        ->and($lines[0])->toBe('id,pos_id,inst_id,asset_class,side,pos_side,margin_mode,close_type,leverage,ccy,size,entry,exit,realised_pnl,pnl,fees,funding,liq_penalty,r,outcome,opened_at,closed_at,proposal_id,agent,setup_tag,origin');

    $row = str_getcsv($lines[1], escape: '');
    $cells = array_combine(str_getcsv($lines[0], escape: ''), $row);
    expect($cells)->toMatchArray([
        'id' => $kept, 'pos_id' => '1777', 'side' => 'short', 'size' => '1000000', 'entry' => '0.3', 'exit' => '0.1',
        'realised_pnl' => '-12.34', 'pnl' => '-12.34', 'fees' => '-0.00000001', 'funding' => '0', 'liq_penalty' => '',
        'r' => '', 'outcome' => 'loss', 'closed_at' => '2026-10-20T10:00:00.123456Z',
        'opened_at' => '2026-10-20T08:00:00.123456Z', 'agent' => "'=HYPERLINK(\"x\")", 'origin' => 'external',
    ]);
    foreach (['size', 'entry', 'exit', 'realised_pnl', 'pnl', 'fees'] as $money) {
        expect($cells[$money])->toMatch('/^-?\d+(\.\d+)?$/')->not->toContain('E');
    }

    $audit = DB::table('audit_log')->where('action', 'portfolio.history_exported')->get();
    expect($audit)->toHaveCount(1)
        ->and(json_decode((string) $audit[0]->after, true))->toMatchArray([
            'type' => 'closed', 'env' => 'demo', 'side' => 'sell', 'win_loss' => 'loss',
            'from_utc' => '2026-10-19T21:00:00Z', 'to_utc' => '2026-10-20T21:00:00Z',
        ]);
});

it('exports fills as CSV with the fills columns', function (): void {
    phFill('2026-10-20 08:00:00', 'sell', '-3.2');
    $lines = array_values(array_filter(explode("\n", $this->get('/api/exports/fills.csv')->assertOk()->streamedContent())));

    expect($lines)->toHaveCount(2)
        ->and($lines[0])->toBe('id,ts,inst_id,asset_class,side,pos_side,price,size,fee,fee_ccy,fill_pnl,exec_type,trade_id,ord_id,order_id,proposal_id,agent,origin')
        ->and($lines[1])->toContain(',sell,net,3350.12,2,-0.67,USDT,-3.2,T,');
});

it('derives R from the linked proposal stop and leaves it null for external positions (AC4)', function (): void {
    $proposal = phProposal('100', '98');
    $linked = phClosed('2026-10-20 10:00:00', 'long', '6', [
        'entry_px' => '100', 'exit_px' => '103', 'proposal_id' => $proposal, 'agent' => 'strategist', 'origin' => 'atlas',
    ]);
    $shortProposal = phProposal('50', '51');
    $short = phClosed('2026-10-20 09:00:00', 'short', '-1', [
        'entry_px' => '50', 'exit_px' => '50.5', 'proposal_id' => $shortProposal, 'origin' => 'atlas',
    ]);
    $external = phClosed('2026-10-20 08:00:00', 'long', '6', ['entry_px' => '100', 'exit_px' => '103']);

    $rows = collect($this->getJson('/api/portfolio/history?type=closed')->assertOk()->json('data'))->keyBy('id');

    expect($rows[$linked]['r'])->toBe('1.50')
        ->and($rows[$linked]['proposal_id'])->toBe($proposal)
        ->and($rows[$linked]['agent'])->toBe('strategist')
        ->and($rows[$short]['r'])->toBe('-0.50')
        ->and($rows[$external]['r'])->toBeNull()
        ->and($rows[$external]['origin'])->toBe('external');

    $csv = $this->get('/api/exports/closed.csv')->streamedContent();
    expect($csv)->toContain(',1.50,win,');
});
