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

it('refuses to run in production', function (): void {
    $this->app['env'] = 'production';

    expect(fn () => app(WarroomDemoSeeder::class)->run())
        ->toThrow(RuntimeException::class, 'refuses to run when APP_ENV=production');
});
