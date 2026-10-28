<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\Setting;
use App\Models\SettingsHistory;
use App\Models\User;
use App\Support\Events\EventBus;
use Illuminate\Support\Facades\Redis;

/**
 * Every system.settings_changed envelope on atlas.system for one settings row (read with XRANGE).
 *
 * @return list<array<string, mixed>>
 */
function settingsChangedEvents(string $settingsId): array
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

it('creates demo settings at version 1 from the balanced defaults on first read', function (): void {
    $this->getJson('/api/settings?env=demo')
        ->assertOk()
        ->assertJsonPath('data.env', 'demo')
        ->assertJsonPath('data.version', 1)
        ->assertJsonPath('data.settings.risk_profile', 'balanced')
        ->assertJsonPath('data.settings.autonomy_level', 2)
        ->assertJsonPath('data.settings.max_loss_per_trade_pct', '1.0')
        ->assertJsonPath('data.settings.max_leverage', 5)
        ->assertJsonPath('data.settings.min_expected_r', '1.8')
        ->assertJsonPath('data.settings.max_instruments_per_day', 5)
        ->assertJsonPath('data.presets.conservative.max_leverage', 2);

    expect(Setting::query()->count())->toBe(1)
        ->and(SettingsHistory::query()->count())->toBe(1);

    // A second read does not create another row or version.
    $this->getJson('/api/settings?env=demo')->assertOk()->assertJsonPath('data.version', 1);
    expect(Setting::query()->count())->toBe(1);
});

it('defaults to the demo env and keeps live separate', function (): void {
    $this->getJson('/api/settings')->assertOk()->assertJsonPath('data.env', 'demo');
    $this->getJson('/api/settings?env=live')->assertOk()->assertJsonPath('data.env', 'live');
    $this->getJson('/api/settings?env=paper')->assertUnprocessable()->assertJsonValidationErrors('env');

    expect(Setting::query()->count())->toBe(2);
});

it('applies the conservative preset, versions it, and records history and audit', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $historyBefore = SettingsHistory::query()->count();
    $auditBefore = AuditLog::query()->count();

    $this->patchJson('/api/settings', [
        'env' => 'demo',
        'version' => 1,
        'changes' => ['risk_profile' => 'conservative'],
    ])
        ->assertOk()
        ->assertJsonPath('data.version', 2)
        ->assertJsonPath('data.settings.risk_profile', 'conservative')
        ->assertJsonPath('data.settings.max_leverage', 2)
        ->assertJsonPath('data.settings.min_expected_r', '2.0')
        ->assertJsonPath('data.settings.max_loss_per_trade_pct', '0.5')
        ->assertJsonPath('data.settings.max_consecutive_losses', 2)
        ->assertJsonPath('data.settings.autonomy_level', 2);

    expect(SettingsHistory::query()->count())->toBe($historyBefore + 1)
        ->and(AuditLog::query()->count())->toBe($auditBefore + 1);

    $setting = Setting::query()->where('env', 'demo')->firstOrFail();
    expect($setting->risk_profile)->toBe('conservative')
        ->and($setting->max_leverage)->toBe(2)
        ->and($setting->max_loss_per_trade_pct)->toBe('0.500000');

    $history = SettingsHistory::query()->where('version', 2)->firstOrFail();
    expect($history->changed_by)->toBe($this->owner->id)
        ->and($history->diff['max_leverage'])->toEqual(['from' => 5, 'to' => 2])
        ->and($history->diff['risk_profile'])->toEqual(['from' => 'balanced', 'to' => 'conservative']);

    $audit = AuditLog::query()->where('action', 'settings.updated')->firstOrFail();
    expect($audit->user_id)->toBe($this->owner->id)
        ->and($audit->subject_type)->toBe('settings')
        ->and($audit->subject_id)->toBe($setting->id)
        ->and($audit->before['version'])->toBe(1)
        ->and($audit->after['version'])->toBe(2)
        ->and($audit->ip)->not->toBeNull();
});

it('publishes system.settings_changed with env and the new version to atlas.system', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();

    $this->patchJson('/api/settings', [
        'env' => 'demo',
        'version' => 1,
        'changes' => ['autonomy_level' => 1],
    ])->assertOk();

    $setting = Setting::query()->where('env', 'demo')->firstOrFail();
    $events = settingsChangedEvents($setting->id);

    expect($events)->toHaveCount(1);
    $event = $events[0];
    expect($event['env'])->toBe('demo')
        ->and($event['source'])->toBe('backend:settings')
        ->and($event['payload']['version'])->toBe(2)
        ->and($event['payload']['changed_fields'])->toBe(['autonomy_level'])
        ->and($event['id'])->toBeString()
        ->and($event['ts'])->toMatch('/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/')
        ->and(array_keys($event))->toBe(['id', 'type', 'ts', 'env', 'source', 'correlation_id', 'payload']);
});

it('returns 409 for a stale version and changes nothing', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['max_leverage' => 3]])
        ->assertOk();

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['max_leverage' => 4]])
        ->assertStatus(409)
        ->assertJsonPath('current_version', 2);

    expect(Setting::query()->where('env', 'demo')->value('max_leverage'))->toBe(3)
        ->and(SettingsHistory::query()->count())->toBe(2);
});

it('rejects out-of-range values with a field error', function (array $changes, string $field): void {
    $this->getJson('/api/settings?env=demo')->assertOk();

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => $changes])
        ->assertUnprocessable()
        ->assertJsonValidationErrors("changes.{$field}");

    expect(Setting::query()->where('env', 'demo')->value('version'))->toBe(1);
})->with([
    'zero loss per trade' => [['max_loss_per_trade_pct' => '0'], 'max_loss_per_trade_pct'],
    'loss per trade over 100' => [['max_loss_per_trade_pct' => '100.01'], 'max_loss_per_trade_pct'],
    'negative percentage' => [['max_position_pct' => '-1'], 'max_position_pct'],
    'percentage as a float' => [['max_daily_loss_pct' => 1.5], 'max_daily_loss_pct'],
    'autonomy 5' => [['autonomy_level' => 5], 'autonomy_level'],
    'autonomy 0' => [['autonomy_level' => 0], 'autonomy_level'],
    'leverage 0' => [['max_leverage' => 0], 'max_leverage'],
    'leverage 126' => [['max_leverage' => 126], 'max_leverage'],
    'leverage as a string' => [['max_leverage' => '5'], 'max_leverage'],
    'instruments 11' => [['max_instruments_per_day' => 11], 'max_instruments_per_day'],
    'instruments 0' => [['max_instruments_per_day' => 0], 'max_instruments_per_day'],
    'unknown profile' => [['risk_profile' => 'yolo'], 'risk_profile'],
]);

it('accepts the range boundaries', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => [
        'max_loss_per_trade_pct' => '100',
        'max_leverage' => 125,
        'autonomy_level' => 4,
        'max_instruments_per_day' => 10,
    ]])->assertOk()->assertJsonPath('data.version', 2);

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 2, 'changes' => [
        'max_loss_per_trade_pct' => '0.01',
        'max_leverage' => 1,
        'autonomy_level' => 1,
        'max_instruments_per_day' => 1,
    ]])->assertOk()->assertJsonPath('data.version', 3);
});

it('refuses unknown fields, an empty change set and a missing version', function (): void {
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['withdraw' => true]])
        ->assertUnprocessable()->assertJsonValidationErrors('changes');
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => []])
        ->assertUnprocessable()->assertJsonValidationErrors('changes');
    $this->patchJson('/api/settings', ['env' => 'demo', 'changes' => ['max_leverage' => 3]])
        ->assertUnprocessable()->assertJsonValidationErrors('version');
});

it('makes the profile custom when one risk field is edited and lists history newest first', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['max_leverage' => 3]])
        ->assertOk()
        ->assertJsonPath('data.version', 2)
        ->assertJsonPath('data.settings.risk_profile', 'custom')
        ->assertJsonPath('data.settings.max_leverage', 3)
        ->assertJsonPath('data.settings.max_loss_per_trade_pct', '1.0');

    $this->getJson('/api/settings/history?env=demo')
        ->assertOk()
        ->assertJsonCount(2, 'data')
        ->assertJsonPath('data.0.version', 2)
        ->assertJsonPath('data.0.settings.risk_profile', 'custom')
        ->assertJsonPath('data.0.diff.max_leverage', ['from' => 5, 'to' => 3])
        ->assertJsonPath('data.0.diff.risk_profile', ['from' => 'balanced', 'to' => 'custom'])
        ->assertJsonPath('data.0.changed_by.id', $this->owner->id)
        ->assertJsonPath('data.1.version', 1)
        ->assertJsonPath('data.1.settings.risk_profile', 'balanced')
        ->assertJsonPath('meta.total', 2);
});

it('keeps the preset when an edit leaves the fields equal to it, and choosing a preset again restores it', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();

    // "5" and "5.0" are the same number: still balanced, nothing changed, so no new version.
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['max_position_pct' => '5.0']])
        ->assertOk()
        ->assertJsonPath('data.version', 1)
        ->assertJsonPath('data.settings.risk_profile', 'balanced');

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['max_leverage' => 3]])
        ->assertJsonPath('data.settings.risk_profile', 'custom');

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 2, 'changes' => ['risk_profile' => 'balanced']])
        ->assertJsonPath('data.version', 3)
        ->assertJsonPath('data.settings.risk_profile', 'balanced')
        ->assertJsonPath('data.settings.max_leverage', 5);
});

it('paginates history', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    foreach ([1, 2, 3] as $version) {
        $this->patchJson('/api/settings', ['env' => 'demo', 'version' => $version, 'changes' => ['max_leverage' => $version + 1]])
            ->assertOk();
    }

    $this->getJson('/api/settings/history?env=demo&per_page=2')
        ->assertOk()
        ->assertJsonCount(2, 'data')
        ->assertJsonPath('data.0.version', 4)
        ->assertJsonPath('meta.last_page', 2);

    $this->getJson('/api/settings/history?env=demo&per_page=2&page=2')
        ->assertJsonPath('data.0.version', 2)
        ->assertJsonPath('data.1.version', 1);
});

it('requires authentication', function (): void {
    app('auth')->forgetGuards();

    $this->getJson('/api/settings?env=demo')->assertUnauthorized();
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['max_leverage' => 3]])
        ->assertUnauthorized();
    $this->getJson('/api/settings/history')->assertUnauthorized();
});
