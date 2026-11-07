<?php

declare(strict_types=1);

use App\Models\User;
use App\Models\WarroomMessage;
use App\Models\WarroomThread;
use Database\Seeders\WarroomDemoSeeder;

it('seeds 500 Main messages ending with an owner message, plus two empty threads', function (): void {
    app(WarroomDemoSeeder::class)->run();

    $main = WarroomThread::query()->where(['env' => 'demo', 'kind' => 'main'])->firstOrFail();
    expect(WarroomMessage::query()->where('thread_id', $main->id)->count())->toBe(WarroomDemoSeeder::COUNT)
        ->and(WarroomThread::query()->where('env', 'demo')->pluck('kind')->sort()->values()->all())->toBe(['agent', 'instrument', 'main']);

    $this->actingAs(User::factory()->create(), 'web');
    $page = $this->getJson('/api/warroom/messages?thread_id='.$main->id)->assertOk()->json();
    expect($page['data'][0])->toMatchArray([
        'author_type' => 'human',
        'content_md' => WarroomDemoSeeder::LAST_OWNER_TEXT,
        'created_at' => '2026-10-28T02:00:00.000000Z',
    ])
        ->and($page['data'][1]['reply_to'])->not->toBeNull()
        ->and($page['meta']['next_cursor'])->not->toBeNull();

    $kinds = WarroomMessage::query()->distinct()->pluck('kind')->sort()->values()->all();
    expect($kinds)->toBe(['system_alert', 'text', 'watchlist']);
});

it('reads like a real day: no counters, no repeated neighbours, a reply, a system line and an alert', function (): void {
    app(WarroomDemoSeeder::class)->run();

    $main = WarroomThread::query()->where(['env' => 'demo', 'kind' => 'main'])->firstOrFail();
    $messages = WarroomMessage::query()->where('thread_id', $main->id)->orderBy('created_at')->get();
    $texts = $messages->pluck('content_md')->all();

    expect($texts)->toHaveCount(WarroomDemoSeeder::COUNT);
    foreach ($texts as $i => $text) {
        expect(preg_match('/Check \\d+|\\(\\d+\\)$/', $text))->toBe(0, "message {$i} carries a counter: {$text}")
            ->and($text)->not->toBe('');
        if ($i > 0) {
            expect($text)->not->toBe($texts[$i - 1], "messages {$i} and ".($i - 1).' are identical');
        }
    }

    $last = $messages->last();
    expect($last?->content_md)->toBe(WarroomDemoSeeder::LAST_OWNER_TEXT)
        ->and($last?->author_type)->toBe('human')
        ->and($last?->created_at?->toIso8601ZuluString())->toBe(WarroomDemoSeeder::LAST_AT);

    $reply = $messages[WarroomDemoSeeder::COUNT - 2];
    $question = $messages->firstWhere('id', $reply->reply_to);
    expect($reply->content_md)->toBe(WarroomDemoSeeder::CPI_REPLY_TEXT)
        ->and($question?->content_md)->toBe(WarroomDemoSeeder::CPI_QUESTION_TEXT)
        ->and($messages->where('author_type', 'system')->where('kind', 'text')->count())->toBeGreaterThan(0)
        ->and($messages->where('kind', 'system_alert')->whereNotNull('card')->count())->toBeGreaterThan(0)
        ->and($messages->where('kind', 'watchlist')->count())->toBeGreaterThan(0)
        ->and($messages->where('author_type', 'agent')->where('kind', 'text')->count())->toBeGreaterThan(0);
});

it('quotes BTC prices in a plausible range and has Arabic owner messages', function (): void {
    app(WarroomDemoSeeder::class)->run();

    $texts = WarroomMessage::query()->pluck('content_md');
    $btc = $texts->filter(fn (string $t): bool => str_contains($t, 'BTC'));
    expect($btc->count())->toBeGreaterThan(3);
    $prices = 0;
    foreach ($btc as $text) {
        preg_match_all('/\\d{1,3}(?:,\\d{3})+(?:\\.\\d+)?|\\d{4,}(?:\\.\\d+)?/', $text, $m);
        foreach ($m[0] as $raw) {
            $price = (float) str_replace(',', '', $raw);
            expect($price)->toBeGreaterThanOrEqual(20_000, "{$raw} in: {$text}")->toBeLessThanOrEqual(200_000, "{$raw} in: {$text}");
            $prices++;
        }
    }
    expect($prices)->toBeGreaterThan(0);

    $arabicOwner = WarroomMessage::query()->where('author_type', 'human')->pluck('content_md')
        ->filter(fn (string $t): bool => preg_match('/\\p{Arabic}/u', $t) === 1);
    expect($arabicOwner->count())->toBeGreaterThanOrEqual(3);
});

it('refuses to run in production', function (): void {
    $this->app['env'] = 'production';

    expect(fn () => app(WarroomDemoSeeder::class)->run())
        ->toThrow(RuntimeException::class, 'refuses to run when APP_ENV=production');
});
