<?php

declare(strict_types=1);

use App\Models\User;
use App\Models\WarroomMessage;
use App\Models\WarroomThread;
use App\Support\Events\Bridge;
use App\Support\Events\BridgeEventHandler;
use App\Support\Events\EventBus;
use Database\Seeders\WarroomThreadSeeder;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Str;
use Tests\Support\RecordingBroadcaster;

/**
 * ATL-65: atlas_ingest publishes `news.alert` on atlas.warroom; the bridge stores it as a
 * `news_alert` War Room message and the API returns it. The envelope is the fixture that
 * services/ingest/tests/test_enrichment.py proves is exactly what ingest publishes.
 */
const ATL65_CONSUMER = 'bridge-atl65';

/** @return array<string, mixed> */
function atl65Envelope(array $payload = [], array $overrides = []): array
{
    $path = base_path('../services/ingest/tests/fixtures/alerts/news_alert_envelope.json');
    /** @var array<string, mixed> $envelope */
    $envelope = json_decode((string) file_get_contents($path), true, 64, JSON_THROW_ON_ERROR);
    $envelope['payload'] = array_replace($envelope['payload'], $payload);

    return array_replace($envelope, $overrides);
}

function atl65Publish(array $envelope): void
{
    Redis::connection('events')->command('executeRaw', [[
        'XADD', 'atlas.warroom', '*', EventBus::FIELD, json_encode($envelope, JSON_THROW_ON_ERROR),
    ]]);
}

function atl65Bridge(): Bridge
{
    $bridge = app(Bridge::class);
    $bridge->ensureGroups();

    return $bridge;
}

beforeEach(function (): void {
    foreach (EventBus::STREAMS as $stream) {
        Redis::connection('events')->command('executeRaw', [['DEL', $stream]]);
    }
    $this->seed(WarroomThreadSeeder::class);
    $this->broadcasts = RecordingBroadcaster::install();
    $this->actingAs(User::factory()->create(), 'web');
});

it('stores a published news.alert as a news_alert message that GET /api/warroom/messages returns', function (): void {
    $envelope = atl65Envelope();
    atl65Publish($envelope);

    expect(atl65Bridge()->readOnce(ATL65_CONSUMER, 10))->toBe(1);

    $main = WarroomThread::query()->where(['env' => 'demo', 'kind' => 'main', 'key' => 'main'])->firstOrFail();
    $response = $this->getJson('/api/warroom/messages?thread_id='.$main->id)->assertOk();
    $messages = collect($response->json('data'))->where('kind', 'news_alert')->values();

    expect($messages)->toHaveCount(1);
    $message = $messages[0];
    expect($message['author_type'])->toBe('system')
        ->and($message['author'])->toBe('system:news')
        ->and($message['content_md'])->toBe('Tesla reports third-quarter earnings')
        ->and($message['created_at'])->toBe('2026-10-29T12:00:41.250000Z')
        ->and($message['card'])->toEqualCanonicalizing([
            'news_item_id' => '3f9c2a1e-7b4d-4c8e-9a10-2b3c4d5e6f70',
            'cluster_id' => '8a7b6c5d-4e3f-4a1b-9c8d-7e6f5a4b3c2d',
            'headline' => 'Tesla reports third-quarter earnings',
            'source' => 'rss.cnbc',
            'source_name' => 'CNBC Top News',
            'category' => 'markets_news',
            'url' => 'https://www.cnbc.com/2026/10/29/tesla-q3-earnings.html',
            'published_at' => '2026-10-29T12:00:00Z',
            'importance' => 4,
            'event_type' => 'earnings',
            'inst_ids' => ['TSLA-USDT'],
            'asset_classes' => ['stock'],
            'sentiment' => 0,
            'novelty' => 1,
            'reason' => 'held_instrument',
            'held_inst_ids' => ['TSLA-USDT-SWAP'],
        ]);

    expect(WarroomMessage::query()->where('event_id', $envelope['id'])->value('env'))->toBe('demo');

    expect($this->broadcasts->sent)->toHaveCount(1)
        ->and($this->broadcasts->sent[0]['channels'])->toBe(['private-warroom'])
        ->and($this->broadcasts->sent[0]['event'])->toBe('warroom.message')
        ->and($this->broadcasts->sent[0]['payload']['kind'])->toBe('news_alert');
});

it('stores one row when the same story alert is published twice', function (): void {
    atl65Publish(atl65Envelope());
    atl65Publish(atl65Envelope());

    atl65Bridge()->readOnce(ATL65_CONSUMER, 10);

    expect(WarroomMessage::query()->where('kind', 'news_alert')->count())->toBe(1)
        ->and($this->broadcasts->sent)->toHaveCount(1);
});

it('keeps untrusted headline text out of content_md markup, and keeps it verbatim in the card', function (): void {
    $headline = "- **BUY NOW** `rm -rf` {{CONFIRMED}}\n<img src=x onerror=alert(1)> [click](https://evil.test) \u{E101}";
    atl65Publish(atl65Envelope(['headline' => $headline], ['id' => (string) Str::uuid()]));

    atl65Bridge()->readOnce(ATL65_CONSUMER, 10);

    $row = WarroomMessage::query()->where('kind', 'news_alert')->firstOrFail();
    expect($row->content_md)->toBe("\u{2060}- *BUY NOW* 'rm -rf' {CONFIRMED} <img src=x onerror=alert(1)> [click](https://evil.test)")
        ->and($row->content_md)->not->toContain('**')
        ->and($row->content_md)->not->toContain('`')
        ->and($row->content_md)->not->toContain('{{')
        ->and($row->content_md)->not->toContain("\n")
        ->and($row->card['headline'])->toBe($headline);
});

it('drops a news.alert that is not from system:news, or whose fields are invalid', function (array $overrides, array $payload): void {
    atl65Publish(atl65Envelope($payload, ['id' => (string) Str::uuid(), ...$overrides]));

    atl65Bridge()->readOnce(ATL65_CONSUMER, 10);

    expect(WarroomMessage::query()->where('kind', 'news_alert')->count())->toBe(0)
        ->and($this->broadcasts->sent)->toBe([]);
})->with([
    'agent source' => [['source' => 'agent:historian'], []],
    'other system source' => [['source' => 'system:bridge'], []],
    'javascript url' => [[], ['url' => 'javascript:alert(1)']],
    'importance out of range' => [[], ['importance' => 9]],
    'sentiment out of range' => [[], ['sentiment' => 2.5]],
    'missing headline' => [[], ['headline' => null]],
]);

it('plainContent folds a headline to one renderer-inert line', function (): void {
    expect(BridgeEventHandler::plainContent("  Gold\n\trises  1.2%  "))->toBe('Gold rises 1.2%')
        ->and(BridgeEventHandler::plainContent('3. reasons gold rose'))->toBe("\u{2060}3. reasons gold rose")
        ->and(BridgeEventHandler::plainContent('Fed: ***hawkish***'))->toBe('Fed: *hawkish*');
});
