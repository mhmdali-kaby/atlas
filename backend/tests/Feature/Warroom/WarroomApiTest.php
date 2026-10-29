<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\User;
use App\Models\WarroomMessage;
use App\Models\WarroomThread;
use App\Support\Events\EventBus;
use App\Support\Warroom\MentionParser;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\Facades\Redis;

function atl38Thread(string $env = 'demo', string $kind = 'main', string $key = 'main'): WarroomThread
{
    return WarroomThread::query()->firstOrCreate(
        ['env' => $env, 'kind' => $kind, 'key' => $key],
        ['title' => $kind === 'main' ? 'Main' : $key],
    );
}

function atl38Message(WarroomThread $thread, CarbonImmutable $at, string $content = 'hi'): WarroomMessage
{
    $message = new WarroomMessage([
        'env' => $thread->env, 'thread_id' => $thread->id, 'author_type' => 'agent',
        'author' => 'historian', 'kind' => 'text', 'content_md' => $content,
    ]);
    $message->created_at = $at;
    $message->updated_at = $at;
    $message->save();

    return $message;
}

/**
 * @return list<array<string, mixed>> decoded envelopes on atlas.warroom
 */
function atl38Envelopes(): array
{
    /** @var list<array{0: string, 1: list<string>}> $entries */
    $entries = Redis::connection('events')->command('executeRaw', [['XRANGE', 'atlas.warroom', '-', '+']]);

    return array_map(
        fn (array $entry): array => json_decode($entry[1][1], true, flags: JSON_THROW_ON_ERROR),
        $entries,
    );
}

beforeEach(function (): void {
    Redis::connection('events')->command('executeRaw', [['DEL', 'atlas.warroom']]);
    $this->user = User::factory()->create(['name' => 'Mhmd']);
    $this->actingAs($this->user, 'web');
    $this->thread = atl38Thread();
});

it('pages 250 messages 100/100/50, newest first, with no duplicates or gaps', function (): void {
    $base = CarbonImmutable::parse('2026-10-29T00:00:00Z');
    $expected = [];
    for ($i = 0; $i < 250; $i++) {
        // Pairs share a timestamp so the id tiebreaker is exercised.
        $expected[] = atl38Message($this->thread, $base->addSeconds(intdiv($i, 2)), "m{$i}")->id;
    }
    // Another thread's messages never leak in.
    atl38Message(atl38Thread('demo', 'instrument', 'BTC-USDT-SWAP'), $base->addHour());

    $seen = [];
    $sizes = [];
    $cursor = null;
    do {
        $response = $this->getJson('/api/warroom/messages?'.http_build_query(array_filter([
            'thread_id' => $this->thread->id, 'limit' => 100, 'cursor' => $cursor,
        ])))->assertOk();
        $rows = $response->json('data');
        $sizes[] = count($rows);
        foreach ($rows as $row) {
            $seen[] = $row;
        }
        $cursor = $response->json('meta.next_cursor');
    } while ($cursor !== null);

    expect($sizes)->toBe([100, 100, 50]);
    $ids = array_column($seen, 'id');
    expect(count(array_unique($ids)))->toBe(250)
        ->and($ids)->toEqualCanonicalizing($expected);

    $keys = array_map(fn (array $r): string => $r['created_at'].'|'.$r['id'], $seen);
    $sorted = $keys;
    rsort($sorted, SORT_STRING);
    expect($keys)->toBe($sorted);
});

it('caps limit at 100 and requires a thread of the env', function (): void {
    $this->getJson('/api/warroom/messages?thread_id='.$this->thread->id.'&limit=101')->assertStatus(422);
    $this->getJson('/api/warroom/messages')->assertStatus(422);
    $live = atl38Thread('live');
    $this->getJson('/api/warroom/messages?thread_id='.$live->id)->assertStatus(422);
    $this->getJson('/api/warroom/messages?env=live&thread_id='.$live->id)->assertOk();
});

it('stores an owner message, parses mentions, audits it and publishes one envelope', function (): void {
    $response = $this->postJson('/api/warroom/messages', [
        'thread_id' => $this->thread->id,
        'content_md' => '@Historian hi',
    ])->assertCreated();

    $message = WarroomMessage::query()->sole();
    expect($message->author_type)->toBe('human')
        ->and($message->author)->toBe('Mhmd')
        ->and($message->kind)->toBe('text')
        ->and($message->env)->toBe('demo')
        ->and($message->mentions)->toBe(['historian'])
        ->and($message->event_id)->not->toBeNull();

    $response->assertJsonPath('data.id', $message->id)
        ->assertJsonPath('data.author_type', 'human')
        ->assertJsonPath('data.mentions', ['historian'])
        ->assertJsonPath('data.tokens_in', null)
        ->assertJsonPath('data.tokens_out', null)
        ->assertJsonPath('data.cost_usd', null);

    $audit = AuditLog::query()->where('action', 'warroom.message_posted')->get();
    expect($audit)->toHaveCount(1)
        ->and($audit[0]->subject_id)->toBe($message->id);

    $envelopes = atl38Envelopes();
    expect($envelopes)->toHaveCount(1)
        ->and($envelopes[0]['id'])->toBe($message->event_id)
        ->and($envelopes[0]['type'])->toBe('warroom.message')
        ->and($envelopes[0]['env'])->toBe('demo')
        ->and($envelopes[0]['source'])->toBe('human:owner')
        ->and($envelopes[0]['payload']['thread'])->toBe(['kind' => 'main', 'key' => 'main'])
        ->and($envelopes[0]['payload']['thread_id'])->toBe($this->thread->id)
        ->and($envelopes[0]['payload']['content_md'])->toBe('@Historian hi')
        ->and($envelopes[0]['payload']['mentions'])->toBe(['historian']);
});

it('accepts a reply in the same thread', function (): void {
    $parent = atl38Message($this->thread, CarbonImmutable::now());

    $this->postJson('/api/warroom/messages', [
        'thread_id' => $this->thread->id, 'content_md' => 'ok', 'reply_to' => $parent->id,
    ])->assertCreated()->assertJsonPath('data.reply_to', $parent->id);
});

it('rejects empty, too long, and cross-thread replies with 422', function (array $body, string $field): void {
    $other = atl38Message(atl38Thread('demo', 'agent', 'historian'), CarbonImmutable::now());
    $body = ['thread_id' => $this->thread->id, ...$body];
    if (($body['reply_to'] ?? null) === 'OTHER') {
        $body['reply_to'] = $other->id;
    }

    $this->postJson('/api/warroom/messages', $body)->assertStatus(422)->assertJsonValidationErrors($field);
    expect(WarroomMessage::query()->where('author_type', 'human')->count())->toBe(0)
        ->and(atl38Envelopes())->toBe([]);
})->with([
    'empty' => [['content_md' => ''], 'content_md'],
    'whitespace' => [['content_md' => '   '], 'content_md'],
    '4001 chars' => [['content_md' => str_repeat('a', 4001)], 'content_md'],
    'reply from another thread' => [['content_md' => 'x', 'reply_to' => 'OTHER'], 'reply_to'],
]);

it('accepts exactly 4000 chars', function (): void {
    $this->postJson('/api/warroom/messages', [
        'thread_id' => $this->thread->id, 'content_md' => str_repeat('a', 4000),
    ])->assertCreated();
});

it('rate-limits posts at 30 per minute', function (): void {
    RateLimiter::clear('warroom-post|'.$this->user->id);
    for ($i = 0; $i < 30; $i++) {
        $this->postJson('/api/warroom/messages', ['thread_id' => $this->thread->id, 'content_md' => "m{$i}"])
            ->assertCreated();
    }
    $this->postJson('/api/warroom/messages', ['thread_id' => $this->thread->id, 'content_md' => 'one more'])
        ->assertStatus(429);
});

it('replays one Baghdad day: 21:00 UTC the day before to 20:59:59 UTC, oldest first', function (): void {
    $other = atl38Thread('demo', 'instrument', 'XAU-USDT-SWAP');
    $before = atl38Message($this->thread, CarbonImmutable::parse('2026-10-27T20:59:59.999999Z'), 'before');
    $first = atl38Message($this->thread, CarbonImmutable::parse('2026-10-27T21:00:00Z'), 'first');
    $middle = atl38Message($other, CarbonImmutable::parse('2026-10-28T08:00:00Z'), 'middle');
    $last = atl38Message($this->thread, CarbonImmutable::parse('2026-10-28T20:59:59Z'), 'last');
    $after = atl38Message($this->thread, CarbonImmutable::parse('2026-10-28T21:00:00Z'), 'after');
    atl38Message(atl38Thread('live'), CarbonImmutable::parse('2026-10-28T08:00:00Z'), 'live');

    $response = $this->getJson('/api/warroom/replay?date=2026-10-28')->assertOk();

    expect(array_column($response->json('data'), 'id'))->toBe([$first->id, $middle->id, $last->id])
        ->and($response->json('meta.from'))->toBe('2026-10-27T21:00:00.000000Z')
        ->and($response->json('meta.to'))->toBe('2026-10-28T21:00:00.000000Z');
    expect([$before->id, $after->id])->each->not->toBeIn(array_column($response->json('data'), 'id'));

    $this->getJson('/api/warroom/replay?date=28-10-2026')->assertStatus(422);
    $this->getJson('/api/warroom/replay')->assertStatus(422);
});

it('lists the env threads with last_message_at, main first', function (): void {
    $empty = atl38Thread('demo', 'agent', 'historian');
    $btc = atl38Thread('demo', 'instrument', 'BTC-USDT-SWAP');
    atl38Message($btc, CarbonImmutable::parse('2026-10-28T08:00:00.5Z'));
    atl38Thread('live');

    $data = $this->getJson('/api/warroom/threads')->assertOk()->json('data');

    expect(array_column($data, 'id'))->toBe([$this->thread->id, $btc->id, $empty->id])
        ->and($data[0])->toHaveKeys(['id', 'kind', 'key', 'title', 'last_message_at', 'created_at'])
        ->and($data[0]['last_message_at'])->toBeNull()
        ->and($data[1]['last_message_at'])->toBe('2026-10-28T08:00:00.500000Z');
});

it('returns 401 for every endpoint when signed out', function (): void {
    auth()->guard('web')->logout();

    $this->getJson('/api/warroom/agents')->assertUnauthorized();
    $this->getJson('/api/warroom/threads')->assertUnauthorized();
    $this->getJson('/api/warroom/messages?thread_id='.$this->thread->id)->assertUnauthorized();
    $this->postJson('/api/warroom/messages', ['thread_id' => $this->thread->id, 'content_md' => 'x'])->assertUnauthorized();
    $this->getJson('/api/warroom/replay?date=2026-10-28')->assertUnauthorized();
    expect(atl38Envelopes())->toBe([]);
});

it('resolves agent mentions by key, spelling and alias', function (): void {
    $parser = new MentionParser;

    expect(MentionParser::agents())->toHaveCount(9)
        ->and($parser->parse('@Historian and @MarketScout, @market_scout again, @Technical @PM'))
        ->toBe(['historian', 'market_scout', 'technical_analyst', 'portfolio_manager'])
        ->and($parser->parse('mail me@historian.com, @nobody'))->toBe([]);
});

it('publishes with a caller-supplied envelope id', function (): void {
    $envelope = app(EventBus::class)->publish('atlas.warroom', 'warroom.message', 'demo', 'test', [], id: '0b8f3c2e-6a51-4f0e-9a43-1c2d7e5b9f10');

    expect($envelope['id'])->toBe('0b8f3c2e-6a51-4f0e-9a43-1c2d7e5b9f10')
        ->and(atl38Envelopes()[0]['id'])->toBe('0b8f3c2e-6a51-4f0e-9a43-1c2d7e5b9f10');
});

it('stores one-word handle mentions: "@Scout and @Devil look"', function (): void {
    $this->postJson('/api/warroom/messages', [
        'thread_id' => $this->thread->id, 'content_md' => '@Scout and @Devil look',
    ])->assertCreated()->assertJsonPath('data.mentions', ['market_scout', 'devils_advocate']);

    expect(WarroomMessage::query()->sole()->mentions)->toBe(['market_scout', 'devils_advocate']);
});

it('resolves every one-word handle to its slug', function (string $text, array $slugs): void {
    expect((new MentionParser)->parse($text))->toBe($slugs);
})->with([
    'Scout' => ['@Scout', ['market_scout']],
    'News' => ['@News', ['news_intelligence']],
    'Technical' => ['@Technical', ['technical_analyst']],
    'Derivatives' => ['@Derivatives', ['derivatives_sentiment']],
    'Devil' => ['@Devil', ['devils_advocate']],
    'PM' => ['@PM', ['portfolio_manager']],
    'Orch' => ['@Orch', ['orchestrator']],
    'Historian' => ['@Historian', ['historian']],
    'Strategist' => ['@strategist', ['strategist']],
    'slug' => ['@devils_advocate', ['devils_advocate']],
]);

it('lists the nine agents with slug, name, handle and aliases in brief order', function (): void {
    $data = $this->getJson('/api/warroom/agents')->assertOk()->json('data');

    expect($data)->toHaveCount(9)
        ->and(array_column($data, 'slug'))->toBe([
            'orchestrator', 'market_scout', 'news_intelligence', 'historian', 'technical_analyst',
            'derivatives_sentiment', 'strategist', 'devils_advocate', 'portfolio_manager',
        ])
        ->and(array_column($data, 'handle'))->toBe([
            'Orch', 'Scout', 'News', 'Historian', 'Technical', 'Derivatives', 'Strategist', 'Devil', 'PM',
        ])
        ->and($data[1])->toBe(['slug' => 'market_scout', 'name' => 'Market Scout', 'handle' => 'Scout', 'aliases' => []])
        ->and($data[7]['name'])->toBe("Devil's Advocate");
    foreach ($data as $agent) {
        expect(array_keys($agent))->toBe(['slug', 'name', 'handle', 'aliases']);
    }
});
