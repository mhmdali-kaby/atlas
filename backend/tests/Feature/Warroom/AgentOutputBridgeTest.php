<?php

declare(strict_types=1);

use App\Models\User;
use App\Models\WarroomMessage;
use App\Models\WarroomThread;
use App\Support\Events\Bridge;
use App\Support\Events\BridgeEventHandler;
use App\Support\Events\EventBus;
use App\Support\Events\MalformedEvent;
use Database\Seeders\WarroomThreadSeeder;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Str;
use Tests\Support\RecordingBroadcaster;

/**
 * ATL-89: what atlas:bridge does with agent output. The envelopes are the exact shapes ATL-88's
 * warroom tools and ThinkingPublisher publish (docs/handoff/ATL-88-warroom-events.md).
 */
const ATL89_CONSUMER = 'bridge-atl89';

/** @return array<string, mixed> */
function atl89HistoryVerdict(): array
{
    return [
        'type' => 'history_verdict',
        'question' => 'Does a 20-day breakout on XAU follow through over 24h?',
        'inst_id' => 'XAU-USDT-SWAP',
        'bar' => '1H',
        'period' => '2024-10-29/2026-10-29',
        'n' => 31,
        'distribution' => [
            '24h' => ['median' => 0.0042, 'p10' => -0.0121, 'p90' => 0.0198, 'pct_positive' => 61.3, 'n' => 31],
        ],
        'base_rate' => [
            '24h' => ['median' => 0.0008, 'p10' => -0.0135, 'p90' => 0.0141, 'pct_positive' => 52.1],
        ],
        'regime' => ['label' => 'trend', 'probability' => 0.78, 'days_in_regime' => 6],
        'stop_noise_check' => null,
        'overfit_warning' => false,
        'verdict' => 'supports',
        'key_stat' => '61.3% of 31 breakouts were up 24h later vs 52.1% base rate',
        'data_quality_notes' => [],
        'data_as_of' => '2026-10-29T07:00:00+00:00',
        'meta' => [
            'numguard' => [
                'unmatched_numbers' => [
                    ['raw' => '0.0731', 'value' => 0.0731, 'kind' => 'number', 'location' => 'card', 'span' => null, 'path' => 'distribution.24h.p90'],
                ],
                'checked' => 16,
            ],
        ],
    ];
}

/** @return list<array<string, mixed>> */
function atl89ToolCalls(): array
{
    return [
        [
            'id' => 'toolu_1', 'name' => 'mcp__history__detect_regime', 'input' => ['inst_id' => 'XAU-USDT-SWAP'],
            'input_truncated' => false, 'as_of' => '2026-10-29T07:00:00+00:00',
            'output' => '{"as_of":"2026-10-29T07:00:00+00:00","source":"atlas_quant","data":{}}',
            'output_truncated' => false, 'is_error' => null,
        ],
        [
            'id' => 'toolu_2', 'name' => 'mcp__history__find_analogs', 'input' => ['inst_id' => 'XAU-USDT-SWAP', 'bar' => '1H'],
            'input_truncated' => false, 'as_of' => '2026-10-29T07:00:00+00:00',
            'output' => '{"as_of":"2026-10-29T07:00:00+00:00","source":"atlas_quant","data":{"n":31}}',
            'output_truncated' => false, 'is_error' => null,
        ],
    ];
}

/** @return array<string, mixed> */
function atl89Message(array $payload = [], array $overrides = []): array
{
    return array_replace([
        'id' => (string) Str::uuid(),
        'type' => 'warroom.message',
        'ts' => '2026-10-29T09:00:00Z',
        'env' => 'demo',
        'source' => 'agent:historian',
        'correlation_id' => 'cycle-2026-10-29',
        'payload' => array_replace([
            'thread' => ['kind' => 'main', 'key' => 'main'],
            'author_type' => 'agent',
            'author' => 'historian',
            'kind' => 'research_report',
            'content_md' => 'History supports the XAU breakout: 61.3% up after 24h (n=31).',
            'card' => atl89HistoryVerdict(),
            'reply_to' => null,
            'mentions' => [],
            'reasoning_md' => "Regime first.\n\nThen analogs.",
            'tool_calls' => atl89ToolCalls(),
            'tokens_in' => 5120,
            'tokens_out' => 812,
            'cost_usd' => '0.041300',
        ], $payload),
    ], $overrides);
}

/** @return array<string, mixed> */
function atl89Thinking(array $payload = []): array
{
    return [
        'id' => (string) Str::uuid(),
        'type' => 'warroom.thinking',
        'ts' => '2026-10-29T09:00:01.2Z',
        'env' => 'demo',
        'source' => 'agent:historian',
        'correlation_id' => 'cycle-1',
        'payload' => array_replace([
            'run_id' => (string) Str::uuid(),
            'agent' => 'historian',
            'seq' => 3,
            'delta_type' => 'thinking',
            'text' => 'Checking the regime before the analogs.',
            'tool' => null,
            'tool_use_id' => null,
            'input' => null,
            'output' => null,
            'is_error' => null,
            'truncated' => false,
        ], $payload),
    ];
}

function atl89Publish(array $envelope): void
{
    Redis::connection('events')->command('executeRaw', [[
        'XADD', 'atlas.warroom', '*', EventBus::FIELD, json_encode($envelope, JSON_THROW_ON_ERROR),
    ]]);
}

function atl89Drain(): void
{
    $bridge = app(Bridge::class);
    $bridge->ensureGroups();
    while ($bridge->readOnce(ATL89_CONSUMER, 10) > 0) {
    }
}

function atl89Main(): WarroomThread
{
    return WarroomThread::query()->where(['env' => 'demo', 'kind' => 'main', 'key' => 'main'])->firstOrFail();
}

beforeEach(function (): void {
    foreach (EventBus::STREAMS as $stream) {
        Redis::connection('events')->command('executeRaw', [['DEL', $stream]]);
    }
    $this->seed(WarroomThreadSeeder::class);
    $this->broadcasts = RecordingBroadcaster::install();
    $this->actingAs(User::factory()->create(), 'web');
    config()->set('atlas_defaults.warroom.thinking_max_bytes', 16384);
});

it('stores an agent research_report card, reasoning and tool calls, and the API returns them', function (): void {
    $envelope = atl89Message();
    atl89Publish($envelope);
    atl89Drain();

    $response = $this->getJson('/api/warroom/messages?thread_id='.atl89Main()->id)->assertOk();
    $messages = collect($response->json('data'))->where('author', 'historian')->values();
    expect($messages)->toHaveCount(1);
    $message = $messages[0];

    expect($message['kind'])->toBe('research_report')
        ->and($message['author_type'])->toBe('agent')
        ->and($message['card'])->toEqual(atl89HistoryVerdict())
        ->and($message['reasoning_md'])->toBe("Regime first.\n\nThen analogs.")
        ->and($message['tool_calls'])->toHaveCount(2)
        ->and($message['tool_calls'])->toEqual(atl89ToolCalls())
        ->and($message['tokens_in'])->toBe(5120)
        ->and($message['tokens_out'])->toBe(812)
        ->and($message['cost_usd'])->toBe('0.041300')
        ->and($message['unmatched_numbers'])->toEqual(atl89HistoryVerdict()['meta']['numguard']['unmatched_numbers']);

    // The live broadcast carries the same resource.
    $sent = collect($this->broadcasts->sent)->where('event', 'warroom.message')->values();
    expect($sent)->toHaveCount(1)
        ->and($sent[0]['channels'])->toBe(['private-warroom'])
        ->and($sent[0]['payload']['tool_calls'])->toEqual(atl89ToolCalls())
        ->and($sent[0]['payload']['unmatched_numbers'])->toHaveCount(1);
});

it('returns unmatched_numbers null without card meta, and reads card.meta.unmatched_numbers too', function (): void {
    $plain = atl89Message(['kind' => 'text', 'card' => null, 'content_md' => 'Done.']);
    $flat = atl89Message(['card' => ['type' => 'note', 'meta' => ['unmatched_numbers' => [['raw' => '14.6%']], 'checked' => 3]]]);
    atl89Publish($plain);
    atl89Publish($flat);
    atl89Drain();

    $data = collect($this->getJson('/api/warroom/messages?thread_id='.atl89Main()->id)->assertOk()->json('data'))
        ->where('author', 'historian')->keyBy('kind');
    expect($data['text']['unmatched_numbers'])->toBeNull()
        ->and($data['research_report']['unmatched_numbers'])->toBe([['raw' => '14.6%']]);
});

it('broadcasts warroom.thinking on private-warroom unchanged and stores no row', function (): void {
    $envelope = atl89Thinking();
    atl89Publish($envelope);
    atl89Drain();

    $sent = collect($this->broadcasts->sent)->where('event', 'warroom.thinking')->values();
    expect($sent)->toHaveCount(1)
        ->and($sent[0]['channels'])->toBe(['private-warroom'])
        ->and($sent[0]['payload'])->toBe($envelope)
        ->and(WarroomMessage::query()->count())->toBe(0)
        ->and(collect($this->broadcasts->sent)->where('event', 'warroom.message'))->toHaveCount(0);
});

it('cuts a thinking payload over the byte cap to the cap and marks it truncated', function (): void {
    config()->set('atlas_defaults.warroom.thinking_max_bytes', 1024);
    $envelope = atl89Thinking([
        'delta_type' => 'tool_result',
        'text' => null,
        'tool' => 'mcp__history__find_analogs',
        'tool_use_id' => 'toolu_2',
        'output' => str_repeat('analog row é ', 400),
    ]);
    atl89Publish($envelope);
    atl89Drain();

    $sent = collect($this->broadcasts->sent)->where('event', 'warroom.thinking')->values();
    expect($sent)->toHaveCount(1);
    $payload = $sent[0]['payload']['payload'];
    $bytes = strlen((string) json_encode($payload));

    expect($payload['truncated'])->toBeTrue()
        ->and($bytes)->toBeLessThanOrEqual(1024)
        ->and($bytes)->toBeGreaterThan(1000)
        ->and(str_starts_with($envelope['payload']['output'], $payload['output']))->toBeTrue()
        ->and(mb_check_encoding($payload['output'], 'UTF-8'))->toBeTrue()
        ->and(array_keys($payload))->toBe(array_keys($envelope['payload']))
        ->and($payload['run_id'])->toBe($envelope['payload']['run_id'])
        ->and($payload['seq'])->toBe(3)
        ->and($sent[0]['payload']['id'])->toBe($envelope['id'])
        ->and(WarroomMessage::query()->count())->toBe(0);
});

it('caps a thinking payload whose input object is the bulk', function (): void {
    $payload = atl89Thinking([
        'delta_type' => 'tool_use', 'text' => null, 'tool' => 'mcp__news__search_news', 'tool_use_id' => 'toolu_3',
        'input' => ['query' => str_repeat('gold ', 500)],
    ])['payload'];

    $capped = BridgeEventHandler::capPayload($payload, 600);
    $small = atl89Thinking()['payload'];

    expect($capped['truncated'])->toBeTrue()
        ->and($capped['input'])->toBeString()
        ->and(str_starts_with($capped['input'], '{"query":"gold gold'))->toBeTrue()
        ->and(strlen((string) json_encode($capped)))->toBeLessThanOrEqual(600)
        ->and(BridgeEventHandler::capPayload($small, 600))->toBe($small)
        ->and(BridgeEventHandler::capPayload(['run_id' => 'r', 'agent' => 'historian', 'seq' => 1, 'delta_type' => 'text', 'tool' => str_repeat('x', 900)], 200))
        ->toBe(['truncated' => true, 'run_id' => 'r', 'agent' => 'historian', 'seq' => 1, 'delta_type' => 'text']);
});

it('still refuses an agent message whose source does not match its author', function (string $source): void {
    $envelope = atl89Message([], ['source' => $source]);

    expect(fn () => app(BridgeEventHandler::class)->handle('atlas.warroom', json_encode($envelope, JSON_THROW_ON_ERROR)))
        ->toThrow(MalformedEvent::class, 'source does not match payload.author');
    expect(WarroomMessage::query()->count())->toBe(0);
})->with(['other agent' => 'agent:market_scout', 'system' => 'system:news', 'unknown agent' => 'agent:intruder']);

it('never stores an agent envelope that claims author_type human', function (): void {
    $envelope = atl89Message(['author_type' => 'human']);

    expect(fn () => app(BridgeEventHandler::class)->handle('atlas.warroom', json_encode($envelope, JSON_THROW_ON_ERROR)))
        ->toThrow(MalformedEvent::class);
    expect(WarroomMessage::query()->count())->toBe(0);
});

it('stores a replayed agent event id once', function (): void {
    $envelope = atl89Message();
    atl89Publish($envelope);
    atl89Publish($envelope);
    atl89Drain();

    expect(WarroomMessage::query()->where('event_id', $envelope['id'])->count())->toBe(1)
        ->and(collect($this->broadcasts->sent)->where('event', 'warroom.message'))->toHaveCount(1)
        ->and(app(BridgeEventHandler::class)->handle('atlas.warroom', json_encode($envelope, JSON_THROW_ON_ERROR)))
        ->toBe(BridgeEventHandler::OUTCOME_DUPLICATE);
});
