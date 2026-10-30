<?php

declare(strict_types=1);

use App\Casts\PostgresTextArray;
use App\Http\Resources\WarroomMessageResource;
use App\Models\WarroomMessage;
use App\Models\WarroomThread;
use Database\Seeders\DatabaseSeeder;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Str;

function mainThread(string $env = 'demo'): WarroomThread
{
    return WarroomThread::query()->firstOrCreate(
        ['env' => $env, 'kind' => 'main', 'key' => 'main'],
        ['title' => 'Main'],
    );
}

it('creates the war room tables and seeds exactly one main thread per env', function (): void {
    expect(Schema::hasTable('warroom_threads'))->toBeTrue()
        ->and(Schema::hasTable('warroom_messages'))->toBeTrue();

    $this->seed(DatabaseSeeder::class);
    $this->seed(DatabaseSeeder::class); // idempotent

    $main = WarroomThread::query()->where('kind', 'main')->orderBy('env')->get();
    expect($main->pluck('env')->all())->toBe(['demo', 'live'])
        ->and($main->pluck('key')->unique()->all())->toBe(['main']);
});

it('refuses a second thread with the same env, kind and key', function (): void {
    mainThread();
    WarroomThread::query()->create(['env' => 'demo', 'kind' => 'main', 'key' => 'main', 'title' => 'Again']);
})->throws(UniqueConstraintViolationException::class);

it('refuses two messages with the same event_id', function (): void {
    $thread = mainThread();
    $eventId = (string) Str::uuid();
    $row = [
        'env' => 'demo', 'thread_id' => $thread->id, 'event_id' => $eventId,
        'author_type' => 'system', 'author' => 'system', 'kind' => 'text', 'content_md' => 'hi',
    ];

    WarroomMessage::query()->create($row);
    WarroomMessage::query()->create($row);
})->throws(UniqueConstraintViolationException::class);

it('allows many messages without an event_id', function (): void {
    $thread = mainThread();
    foreach ([1, 2] as $_) {
        WarroomMessage::query()->create([
            'env' => 'demo', 'thread_id' => $thread->id, 'author_type' => 'human',
            'author' => 'owner', 'kind' => 'text', 'content_md' => 'hello',
        ]);
    }

    expect(WarroomMessage::query()->count())->toBe(2);
});

it('round-trips mentions through the text[] column', function (): void {
    $mentions = ['historian', 'market scout', 'quote"d', 'back\\slash', 'a,b', '{x}'];
    $message = WarroomMessage::query()->create([
        'env' => 'demo', 'thread_id' => mainThread()->id, 'author_type' => 'human', 'author' => 'owner',
        'kind' => 'text', 'content_md' => '@historian', 'mentions' => $mentions,
    ]);

    expect($message->fresh()?->mentions)->toBe($mentions)
        ->and(PostgresTextArray::parse('{}'))->toBe([])
        ->and(PostgresTextArray::parse('{a,"b c"}'))->toBe(['a', 'b c']);

    $empty = WarroomMessage::query()->create([
        'env' => 'demo', 'thread_id' => mainThread()->id, 'author_type' => 'human', 'author' => 'owner',
        'kind' => 'text', 'content_md' => 'no mentions',
    ]);
    expect($empty->fresh()?->mentions)->toBe([]);
});

it('renders a message through WarroomMessageResource', function (): void {
    Carbon::setTestNow(Carbon::parse('2026-10-29T08:00:00.123456+03:00'));
    $thread = mainThread();
    $parent = WarroomMessage::query()->create([
        'env' => 'demo', 'thread_id' => $thread->id, 'author_type' => 'human', 'author' => 'owner',
        'kind' => 'text', 'content_md' => 'question',
    ]);
    $message = WarroomMessage::query()->create([
        'env' => 'demo', 'thread_id' => $thread->id, 'event_id' => (string) Str::uuid(),
        'author_type' => 'agent', 'author' => 'historian', 'kind' => 'research_report',
        'content_md' => '**Verdict:** weakly supports', 'card' => ['n' => 14, 'verdict' => 'weakly_supports'],
        'reply_to' => $parent->id, 'mentions' => ['market_scout'], 'reasoning_md' => 'n < 20',
        'tool_calls' => [['tool' => 'history.compute_stats', 'input' => ['inst_id' => 'BTC-USDT-SWAP']]],
        'tokens_in' => 1200, 'tokens_out' => 340, 'cost_usd' => '0.0123',
    ]);

    $json = (new WarroomMessageResource($message->fresh()))->response()->getData(true);

    expect($json['data'])->toBe([
        'id' => $message->id,
        'thread_id' => $thread->id,
        'author_type' => 'agent',
        'author' => 'historian',
        'kind' => 'research_report',
        'content_md' => '**Verdict:** weakly supports',
        'card' => ['n' => 14, 'verdict' => 'weakly_supports'],
        'reply_to' => $parent->id,
        'mentions' => ['market_scout'],
        'reasoning_md' => 'n < 20',
        'tool_calls' => [['tool' => 'history.compute_stats', 'input' => ['inst_id' => 'BTC-USDT-SWAP']]],
        'tokens_in' => 1200,
        'tokens_out' => 340,
        'cost_usd' => '0.012300',
        'unmatched_numbers' => null,
        'created_at' => '2026-10-29T05:00:00.123456Z',
    ]);

    $bare = (new WarroomMessageResource($parent->fresh()))->response()->getData(true)['data'];
    expect($bare['card'])->toBeNull()
        ->and($bare['mentions'])->toBe([])
        ->and($bare['cost_usd'])->toBeNull()
        ->and($bare['tokens_in'])->toBeNull();
});
