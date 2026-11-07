<?php

declare(strict_types=1);

use App\Models\User;
use App\Support\Settings\SettingsSchema;
use Database\Seeders\NewsSourcesSeeder;
use Database\Seeders\ResearchDemoSeeder;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\DB;

const RESEARCH_TABLES = ['news_items', 'news_clusters', 'economic_events', 'earnings_events', 'journal_entries', 'agent_scores', 'agent_runs'];

/** @return array<string, int> */
function researchCounts(): array
{
    return collect(RESEARCH_TABLES)->mapWithKeys(fn (string $t): array => [$t => DB::table($t)->count()])->all();
}

it('seeds through db:seed and leaves the same row counts when run twice', function (): void {
    expect(Artisan::call('db:seed', ['--class' => ResearchDemoSeeder::class, '--no-interaction' => true]))->toBe(0);
    $first = researchCounts();

    expect(Artisan::call('db:seed', ['--class' => ResearchDemoSeeder::class, '--no-interaction' => true]))->toBe(0);

    expect(researchCounts())->toBe($first)
        ->and($first['news_items'])->toBeGreaterThanOrEqual(20)
        ->and($first['journal_entries'])->toBe(6)
        ->and($first['agent_scores'])->toBe(ResearchDemoSeeder::SCORE_DAYS * count(SettingsSchema::agentSlugs()));
});

it('refuses to run in production and writes nothing', function (): void {
    app()->detectEnvironment(fn (): string => 'production');
    try {
        expect(fn () => app(ResearchDemoSeeder::class)->run())->toThrow(RuntimeException::class);
    } finally {
        app()->detectEnvironment(fn (): string => 'testing');
    }

    expect(array_sum(researchCounts()))->toBe(0);
});

it('fills the News feed and the calendar', function (): void {
    $this->seed(NewsSourcesSeeder::class);
    app(ResearchDemoSeeder::class)->run();
    $this->actingAs(User::factory()->create(), 'web');

    $items = $this->getJson('/api/news?limit=100')->assertOk()->json('data');
    expect(count($items))->toBeGreaterThanOrEqual(20)
        ->and(collect($items)->where('language', 'ar')->count())->toBeGreaterThanOrEqual(3);
    foreach ($items as $item) {
        expect(strtotime($item['published_at']))->toBeGreaterThan(time() - 48 * 3600)->toBeLessThanOrEqual(time());
    }

    $multiSource = collect($items)->whereNotNull('cluster_id')->groupBy('cluster_id')
        ->filter(fn ($group): bool => $group->pluck('source')->unique()->count() >= 2);
    expect($multiSource->count())->toBeGreaterThanOrEqual(2);

    $clusterId = $multiSource->keys()->first();
    expect($this->getJson("/api/news/clusters/{$clusterId}")->assertOk()->json('data.item_count'))->toBeGreaterThanOrEqual(2);

    $events = collect($this->getJson('/api/calendar')->assertOk()->json('data'));
    $cpi = $events->first(fn (array $e): bool => $e['kind'] === 'economic' && $e['country'] === 'US' && $e['name'] === 'CPI m/m');
    expect($cpi)->not->toBeNull()
        ->and($cpi['forecast'])->toBe('0.3%')
        ->and($cpi['previous'])->toBe('0.4%')
        ->and($events->contains(fn (array $e): bool => str_contains((string) ($e['name'] ?? ''), 'FOMC')))->toBeTrue()
        ->and($events->contains(fn (array $e): bool => $e['kind'] === 'earnings' && $e['symbol'] === 'NVDA'))->toBeTrue();
});

it('fills the Journal and 14 days of scores for every agent, and today\'s usage', function (): void {
    app(ResearchDemoSeeder::class)->run();
    $this->actingAs(User::factory()->create(), 'web');

    $entries = collect($this->getJson('/api/journal')->assertOk()->json('data'));
    expect($entries)->toHaveCount(6)
        ->and($entries->filter(fn (array $e): bool => preg_match('/\p{Arabic}/u', $e['lessons_md'].$e['title']) === 1)->count())->toBe(2)
        ->and($entries->pluck('data.outcome')->unique()->sort()->values()->all())->toBe(['loss', 'win']);

    $agents = collect($this->getJson('/api/agents/scores')->assertOk()->json('data'))->keyBy('agent');
    foreach (SettingsSchema::agentSlugs() as $slug) {
        expect($agents[$slug]['days'] ?? [])->toHaveCount(ResearchDemoSeeder::SCORE_DAYS, "{$slug} days")
            ->and($agents[$slug]['summary']['n'])->toBeGreaterThan(0);
    }

    $roster = collect($this->getJson('/api/agents')->assertOk()->json('data'));
    expect($roster)->not->toBeEmpty();
    $today = DB::table('agent_runs')->where('env', 'demo')->selectRaw('agent, sum(turns) as turns')->groupBy('agent')->pluck('turns', 'agent');
    expect($today->keys()->sort()->values()->all())->toBe(collect(SettingsSchema::agentSlugs())->sort()->values()->all());
});

it('seeds no placeholder text', function (): void {
    app(ResearchDemoSeeder::class)->run();

    $strings = [];
    $walk = function (mixed $value) use (&$walk, &$strings): void {
        if (is_array($value) || is_object($value)) {
            foreach ((array) $value as $k => $v) {
                $strings[] = (string) $k;
                $walk($v);
            }
        } elseif (is_string($value)) {
            $decoded = json_decode($value, true);
            is_array($decoded) ? $walk($decoded) : $strings[] = $value;
        }
    };
    foreach (RESEARCH_TABLES as $table) {
        foreach (DB::table($table)->get() as $row) {
            $walk((array) $row);
        }
    }

    expect($strings)->not->toBeEmpty();
    foreach ($strings as $s) {
        expect(preg_match('/lorem|ipsum|\btest\b|sample|foo|example\.com/i', $s))->toBe(0, "placeholder text: {$s}");
    }
});
