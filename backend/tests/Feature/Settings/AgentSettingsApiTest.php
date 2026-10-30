<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Setting;
use App\Models\SettingsHistory;
use App\Models\User;
use App\Support\Events\EventBus;
use Illuminate\Support\Facades\Redis;

/**
 * ATL-113: agent settings (agents.<slug>.enabled|model|daily_tokens|daily_turns and
 * agents.web.allow_domains|deny_domains) live in the versioned settings table.
 */

/**
 * @return list<array<string, mixed>>
 */
function atl113SettingsEvents(string $settingsId): array
{
    /** @var list<array{0: string, 1: list<string>}> $entries */
    $entries = Redis::connection('events')->command('executeRaw', [['XRANGE', 'atlas.system', '-', '+']]);

    $found = [];
    foreach ($entries as [, $fields]) {
        $map = [];
        for ($i = 0; $i + 1 < count($fields); $i += 2) {
            $map[$fields[$i]] = $fields[$i + 1];
        }
        $envelope = json_decode($map[EventBus::FIELD] ?? 'null', true);
        if (is_array($envelope)
            && $envelope['type'] === 'system.settings_changed'
            && ($envelope['payload']['settings_id'] ?? null) === $settingsId) {
            $found[] = $envelope;
        }
    }

    return $found;
}

beforeEach(function (): void {
    $this->owner = User::factory()->create();
    $this->actingAs($this->owner, 'web');
});

it('starts every roster agent from the config/defaults.yaml agent defaults', function (): void {
    $settings = $this->getJson('/api/settings?env=demo')->assertOk()->json('data.settings');

    expect($settings['agents.historian.enabled'])->toBeTrue()
        ->and($settings['agents.historian.model'])->toBe('strong')
        ->and($settings['agents.market_scout.enabled'])->toBeTrue()
        ->and($settings['agents.market_scout.model'])->toBe('strong')
        ->and($settings['agents.orchestrator.model'])->toBe('standard')
        ->and($settings['agents.orchestrator.daily_tokens'])->toBe(3000000)
        ->and($settings['agents.orchestrator.daily_turns'])->toBe(300)
        ->and($settings['agents.web.allow_domains'])->toBe([])
        ->and($settings['agents.web.deny_domains'])->toBe([]);

    foreach (['orchestrator', 'market_scout', 'news_intelligence', 'historian', 'technical_analyst',
        'derivatives_sentiment', 'strategist', 'devils_advocate', 'portfolio_manager'] as $slug) {
        foreach (['enabled', 'model', 'daily_tokens', 'daily_turns'] as $setting) {
            expect($settings)->toHaveKey("agents.{$slug}.{$setting}");
        }
    }
});

it('versions agents.technical_analyst.enabled=false with history, audit and settings_changed', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    // Start from enabled, so the PATCH below is a real change.
    $this->patchJson('/api/settings', [
        'env' => 'demo', 'version' => 1, 'changes' => ['agents.technical_analyst.enabled' => true],
    ])->assertOk()->assertJsonPath('data.version', 2);

    $setting = Setting::query()->where('env', 'demo')->firstOrFail();
    $historyBefore = SettingsHistory::query()->count();
    $auditBefore = AuditLog::query()->where('action', 'settings.updated')->count();
    $eventsBefore = count(atl113SettingsEvents($setting->id));

    $response = $this->patchJson('/api/settings', [
        'env' => 'demo', 'version' => 2, 'changes' => ['agents.technical_analyst.enabled' => false],
    ])->assertOk();

    expect($response->json('data.version'))->toBe(3)
        ->and($response->json('data.settings')['agents.technical_analyst.enabled'])->toBeFalse();

    $setting->refresh();
    expect($setting->version)->toBe(3)
        ->and($setting->data['agents.technical_analyst.enabled'])->toBeFalse()
        // Stored flat, with no nested "agents" object.
        ->and(array_key_exists('agents', $setting->data))->toBeFalse();

    $history = SettingsHistory::query()->where('settings_id', $setting->id)->where('version', 3)->firstOrFail();
    expect(SettingsHistory::query()->count())->toBe($historyBefore + 1)
        ->and($history->diff)->toEqual(['agents.technical_analyst.enabled' => ['from' => true, 'to' => false]]);

    $audit = AuditLog::query()->where('action', 'settings.updated')->get()
        ->firstOrFail(fn (AuditLog $row): bool => ($row->after['version'] ?? null) === 3);
    expect(AuditLog::query()->where('action', 'settings.updated')->count())->toBe($auditBefore + 1)
        ->and($audit->before['data']['agents.technical_analyst.enabled'])->toBeTrue()
        ->and($audit->after['data']['agents.technical_analyst.enabled'])->toBeFalse();

    $events = atl113SettingsEvents($setting->id);
    expect($events)->toHaveCount($eventsBefore + 1);
    $last = end($events);
    expect($last['payload']['version'])->toBe(3)
        ->and($last['payload']['changed_fields'])->toBe(['agents.technical_analyst.enabled'])
        ->and($last['env'])->toBe('demo');
});

it('saves model, budgets and domain lists, normalising hostnames to lower-case punycode', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();

    $response = $this->patchJson('/api/settings', [
        'env' => 'demo',
        'version' => 1,
        'changes' => [
            'agents.news_intelligence.model' => 'routine',
            'agents.news_intelligence.daily_tokens' => 500000,
            'agents.news_intelligence.daily_turns' => 40,
            'agents.web.allow_domains' => ['CoinDesk.com', 'www.reuters.com.', 'coindesk.com'],
            'agents.web.deny_domains' => ['bücher.example', 'XN--BCHER-KVA.example2'],
        ],
    ])->assertOk();

    $settings = $response->json('data.settings');
    expect($settings['agents.news_intelligence.model'])->toBe('routine')
        ->and($settings['agents.news_intelligence.daily_tokens'])->toBe(500000)
        ->and($settings['agents.news_intelligence.daily_turns'])->toBe(40)
        ->and($settings['agents.web.allow_domains'])->toBe(['coindesk.com', 'www.reuters.com'])
        ->and($settings['agents.web.deny_domains'])->toBe(['xn--bcher-kva.example', 'xn--bcher-kva.example2']);

    $stored = Setting::query()->where('env', 'demo')->firstOrFail()->data;
    expect($stored['agents.web.deny_domains'])->toBe(['xn--bcher-kva.example', 'xn--bcher-kva.example2']);
});

it('refuses invalid agent settings with field errors and changes nothing', function (array $changes, string $errorKey): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $before = Setting::query()->where('env', 'demo')->firstOrFail();
    $historyBefore = SettingsHistory::query()->count();
    $auditBefore = AuditLog::query()->count();

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => $changes])
        ->assertUnprocessable()
        ->assertJsonValidationErrors([$errorKey]);

    $after = Setting::query()->where('env', 'demo')->firstOrFail();
    expect($after->version)->toBe(1)
        ->and($after->data)->toBe($before->data)
        ->and(SettingsHistory::query()->count())->toBe($historyBefore)
        ->and(AuditLog::query()->count())->toBe($auditBefore);
})->with([
    'model gpt' => [['agents.historian.model' => 'gpt'], 'changes.agents.historian.model'],
    'daily_tokens -1' => [['agents.historian.daily_tokens' => -1], 'changes.agents.historian.daily_tokens'],
    'daily_tokens as string' => [['agents.historian.daily_tokens' => '500'], 'changes.agents.historian.daily_tokens'],
    'daily_turns 0' => [['agents.historian.daily_turns' => 0], 'changes.agents.historian.daily_turns'],
    'enabled as string' => [['agents.historian.enabled' => 'false'], 'changes.agents.historian.enabled'],
    'deny entry is a URL' => [['agents.web.deny_domains' => ['http://x/']], 'changes.agents.web.deny_domains.0'],
    'allow entry has a port' => [['agents.web.allow_domains' => ['ok.com', 'x.com:443']], 'changes.agents.web.allow_domains.1'],
    'allow entry has a path' => [['agents.web.allow_domains' => ['x.com/news']], 'changes.agents.web.allow_domains.0'],
    'deny list not a list' => [['agents.web.deny_domains' => 'x.com'], 'changes.agents.web.deny_domains'],
    'unknown agent' => [['agents.trader.enabled' => true], 'changes'],
]);

it('refuses a domain list over the 200-entry cap', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $hosts = array_map(static fn (int $i): string => "h{$i}.example", range(1, 201));

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['agents.web.deny_domains' => $hosts]])
        ->assertUnprocessable()
        ->assertJsonValidationErrors(['changes.agents.web.deny_domains']);

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['agents.web.deny_domains' => array_slice($hosts, 0, 200)]])
        ->assertOk()
        ->assertJsonPath('data.version', 2);
});

it('clears a domain list with an empty array', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['agents.web.deny_domains' => ['x.com']]])->assertOk();
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 2, 'changes' => ['agents.web.deny_domains' => []]])
        ->assertOk()
        ->assertJsonPath('data.version', 3);

    expect(Setting::query()->where('env', 'demo')->firstOrFail()->data['agents.web.deny_domains'])->toBe([]);
});
