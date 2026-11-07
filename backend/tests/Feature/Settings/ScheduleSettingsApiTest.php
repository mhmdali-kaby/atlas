<?php

declare(strict_types=1);

use App\Models\Setting;
use App\Models\SettingsHistory;
use App\Models\User;
use App\Support\Events\EventBus;
use App\Support\Settings\SettingsSchema;
use App\Support\Settings\SettingsService;
use Illuminate\Support\Facades\Redis;
use Illuminate\Testing\TestResponse;

/** ATL-228, decision #255 (2): Settings → Schedule as versioned schedule.* settings keys. */
beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
});

function patchSchedule(array $changes, int $version = 1, string $lang = 'en'): TestResponse
{
    return test()->withHeaders(['Accept-Language' => $lang])
        ->patchJson('/api/settings', ['env' => 'demo', 'version' => $version, 'changes' => $changes]);
}

it('returns every schedule key with the defaults.yaml values', function (): void {
    $settings = $this->getJson('/api/settings?env=demo')->assertOk()->json('data.settings');

    expect(array_intersect_key($settings, array_flip(SettingsSchema::scheduleFields())))->toBe([
        'schedule.news_morning_brief' => '07:30',
        'schedule.historian_regime_table' => '07:30',
        'schedule.scout_watchlist' => '08:00',
        'schedule.pm_daily_report' => '23:30',
        'schedule.historian_weekly_review' => 'Sun 18:00',
        'schedule.analysis_interval_minutes' => 30,
        'schedule.analysis_first_slot' => '08:10',
        'schedule.analysis_last_slot' => '23:00',
        'schedule.quiet_hours_enabled' => false,
        'schedule.quiet_hours_start' => '23:00',
        'schedule.quiet_hours_end' => '07:00',
        'schedule.stock_perps_weekend' => 'allow',
    ]);
});

it('fills schedule defaults into a settings row saved before the keys existed', function (): void {
    $setting = app(SettingsService::class)->current('demo');
    $data = $setting->data;
    foreach (SettingsSchema::scheduleFields() as $field) {
        unset($data[$field]);
    }
    $setting->data = $data;
    $setting->save();

    $this->getJson('/api/settings?env=demo')->assertOk()
        ->assertJsonPath('data.settings', fn (array $s): bool => $s['schedule.scout_watchlist'] === '08:00' && $s['schedule.analysis_interval_minutes'] === 30);
});

it('saves an interval change as a new version with history and system.settings_changed', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    $historyBefore = SettingsHistory::query()->count();

    patchSchedule(['schedule.analysis_interval_minutes' => 60])
        ->assertOk()
        ->assertJsonPath('data.version', 2)
        ->assertJsonPath('data.settings', fn (array $s): bool => $s['schedule.analysis_interval_minutes'] === 60);

    $history = SettingsHistory::query()->latest('version')->firstOrFail();
    expect(SettingsHistory::query()->count())->toBe($historyBefore + 1)
        ->and($history->version)->toBe(2)
        ->and($history->diff)->toEqual(['schedule.analysis_interval_minutes' => ['from' => 30, 'to' => 60]]);

    /** @var list<array{0: string, 1: list<string>}> $entries */
    $entries = Redis::connection('events')->command('executeRaw', [['XRANGE', EventBus::streamKey(SettingsService::EVENT_STREAM), '-', '+']]);
    $settingsId = Setting::query()->where('env', 'demo')->value('id');
    $events = [];
    foreach ($entries as [, $fields]) {
        $map = [];
        for ($i = 0; $i + 1 < count($fields); $i += 2) {
            $map[$fields[$i]] = $fields[$i + 1];
        }
        $envelope = json_decode($map[EventBus::FIELD] ?? 'null', true);
        if (is_array($envelope) && $envelope['type'] === SettingsService::EVENT_TYPE && ($envelope['payload']['settings_id'] ?? null) === $settingsId) {
            $events[] = $envelope;
        }
    }
    expect($events)->toHaveCount(1)
        ->and($events[0]['payload']['version'])->toBe(2)
        ->and($events[0]['payload']['changed_fields'])->toBe(['schedule.analysis_interval_minutes']);
});

it('accepts every schedule key and quiet hours that cross midnight', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();

    patchSchedule([
        'schedule.news_morning_brief' => '06:45',
        'schedule.historian_regime_table' => '07:00',
        'schedule.scout_watchlist' => '07:15',
        'schedule.pm_daily_report' => '22:00',
        'schedule.historian_weekly_review' => 'Sat 09:30',
        'schedule.analysis_first_slot' => '09:00',
        'schedule.analysis_last_slot' => '21:00',
        'schedule.quiet_hours_enabled' => true,
        'schedule.quiet_hours_start' => '23:00',
        'schedule.quiet_hours_end' => '07:00',
        'schedule.stock_perps_weekend' => 'no_new_trades',
    ])->assertOk()->assertJsonPath('data.version', 2);

    $data = Setting::query()->where('env', 'demo')->firstOrFail()->data;
    expect($data['schedule.quiet_hours_enabled'])->toBeTrue()
        ->and($data['schedule.quiet_hours_start'])->toBe('23:00')
        ->and($data['schedule.quiet_hours_end'])->toBe('07:00')
        ->and($data['schedule.historian_weekly_review'])->toBe('Sat 09:30')
        ->and($data['schedule.stock_perps_weekend'])->toBe('no_new_trades');
});

it('refuses an invalid single schedule value with 422 naming the field, in English and Arabic', function (string $field, mixed $value, string $en, string $ar): void {
    $this->getJson('/api/settings?env=demo')->assertOk();

    $message = patchSchedule([$field => $value])
        ->assertUnprocessable()
        ->assertJsonValidationErrors("changes.{$field}")
        ->json('errors')["changes.{$field}"][0];
    expect($message)->toContain($en)->not->toContain('_');

    $message = patchSchedule([$field => $value], 1, 'ar')
        ->assertUnprocessable()
        ->json('errors')["changes.{$field}"][0];
    expect($message)->toContain($ar)->not->toContain('_');

    expect(Setting::query()->where('env', 'demo')->value('version'))->toBe(1);
})->with([
    '25:00' => ['schedule.news_morning_brief', '25:00', 'News morning brief time', 'وقت الموجز الإخباري الصباحي'],
    '7:30 without padding' => ['schedule.pm_daily_report', '7:30', 'Portfolio Manager daily report time', 'وقت التقرير اليومي لمدير المحفظة'],
    'interval 5' => ['schedule.analysis_interval_minutes', 5, 'Analysis interval', 'الفاصل بين دورات التحليل'],
    'interval 241' => ['schedule.analysis_interval_minutes', 241, 'Analysis interval', 'الفاصل بين دورات التحليل'],
    'interval as string' => ['schedule.analysis_interval_minutes', '60', 'Analysis interval', 'الفاصل بين دورات التحليل'],
    'weekly without a day' => ['schedule.historian_weekly_review', '18:00', 'Historian weekly review', 'المراجعة الأسبوعية للمؤرّخ'],
    'weekly bad day' => ['schedule.historian_weekly_review', 'Sunday 18:00', 'Historian weekly review', 'المراجعة الأسبوعية للمؤرّخ'],
    'weekend choice' => ['schedule.stock_perps_weekend', 'never', 'Stock perpetuals at the weekend', 'عقود الأسهم الدائمة في عطلة نهاية الأسبوع'],
    'quiet hours as string' => ['schedule.quiet_hours_enabled', 'yes', 'Quiet hours', 'ساعات الهدوء'],
]);

it('refuses an out-of-order schedule with 422 on the changed field, in English and Arabic', function (array $changes, string $field, string $en, string $ar): void {
    $this->getJson('/api/settings?env=demo')->assertOk();

    $message = patchSchedule($changes)
        ->assertUnprocessable()
        ->assertJsonValidationErrors("changes.{$field}")
        ->json('errors')["changes.{$field}"][0];
    expect($message)->toContain($en)->not->toContain('_');

    $message = patchSchedule($changes, 1, 'ar')
        ->assertUnprocessable()
        ->json('errors')["changes.{$field}"][0];
    expect($message)->toContain($ar)->not->toContain('_');

    expect(Setting::query()->where('env', 'demo')->value('version'))->toBe(1)
        ->and(SettingsHistory::query()->where('version', 2)->exists())->toBeFalse();
})->with([
    'first slot after last' => [['schedule.analysis_first_slot' => '23:30'], 'schedule.analysis_first_slot', 'First analysis cycle must be earlier than the Last analysis cycle', 'أول دورة تحليل'],
    'last slot before first' => [['schedule.analysis_last_slot' => '08:00'], 'schedule.analysis_last_slot', 'First analysis cycle must be earlier', 'آخر دورة تحليل'],
    'first equals last' => [['schedule.analysis_first_slot' => '12:00', 'schedule.analysis_last_slot' => '12:00'], 'schedule.analysis_last_slot', 'must be earlier', 'أول دورة تحليل'],
    'scout before the news brief' => [['schedule.scout_watchlist' => '07:00'], 'schedule.scout_watchlist', 'Market Scout watchlist time must be later than the News morning brief time', 'وقت الموجز الإخباري الصباحي'],
    'news brief moved after scout' => [['schedule.news_morning_brief' => '08:30'], 'schedule.news_morning_brief', 'must be later than the News morning brief time', 'وقت قائمة المراقبة من مستكشف السوق'],
    'scout at the same time as the regime table' => [['schedule.scout_watchlist' => '07:30', 'schedule.news_morning_brief' => '07:00'], 'schedule.scout_watchlist', 'Historian regime table time', 'جدول أنظمة السوق'],
    'empty quiet hours' => [['schedule.quiet_hours_start' => '07:00'], 'schedule.quiet_hours_start', 'Quiet hours must start and end at different times', 'ساعات الهدوء'],
]);

it('checks cross-field rules against the stored values, not only the defaults', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    patchSchedule(['schedule.analysis_last_slot' => '20:00'])->assertOk();

    patchSchedule(['schedule.analysis_first_slot' => '21:00'], 2)
        ->assertUnprocessable()
        ->assertJsonValidationErrors('changes.schedule.analysis_first_slot');
    patchSchedule(['schedule.analysis_first_slot' => '19:00'], 2)->assertOk();
});
