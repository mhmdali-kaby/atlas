<?php

declare(strict_types=1);

namespace App\Support\Agents;

use App\Support\Settings\SettingsSchema;
use App\Support\Settings\SettingsService;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;

/**
 * ATL-113, brief §14.7/§16: each roster agent's settings, live state and today's usage for the
 * Agents page. Reads Postgres only (settings, agent_states, agent_runs, prompt_versions); the
 * agents supervisor writes those tables.
 *
 * "Today" is the owner's day (timezone.display, Asia/Baghdad): runs whose started_at falls in
 * [Baghdad midnight, next Baghdad midnight). Tokens are tokens_in + tokens_out, the same sum the
 * runtime budgets against (agents.defaults.daily_tokens).
 */
final class AgentRoster
{
    public function __construct(private readonly SettingsService $settings) {}

    /**
     * @return array{data: list<array<string, mixed>>, meta: array<string, mixed>}
     */
    public function list(string $env, ?CarbonImmutable $now = null): array
    {
        $setting = $this->settings->current($env);
        $values = $this->settings->values($setting);
        [$dayStart, $dayEnd] = self::dayWindowUtc($now ?? CarbonImmutable::now());
        $slugs = SettingsSchema::agentSlugs();

        $usage = $this->usage($env, $slugs, $dayStart, $dayEnd);
        $states = $this->states($env, $slugs);
        $lastRuns = $this->lastRuns($env, $slugs);
        $prompts = $this->prompts($slugs, $lastRuns);

        $directory = config('atlas_defaults.warroom.agents');
        $directory = is_array($directory) ? $directory : [];
        $modelNames = config('atlas_defaults.agents.models');
        $modelNames = is_array($modelNames) ? $modelNames : [];

        $agents = [];
        foreach ($slugs as $slug) {
            $entry = is_array($directory[$slug] ?? null) ? $directory[$slug] : [];
            $enabled = ($values[SettingsSchema::agentField($slug, 'enabled')] ?? false) === true;
            $model = self::str($values[SettingsSchema::agentField($slug, 'model')] ?? null);
            $state = $states[$slug] ?? null;
            $used = $usage[$slug] ?? ['tokens_in' => 0, 'tokens_out' => 0, 'turns' => 0, 'runs' => 0, 'cost' => '0.000000'];
            $run = $lastRuns[$slug] ?? null;

            $agents[] = [
                'slug' => $slug,
                'name' => self::str($entry['name'] ?? $slug),
                'handle' => self::str($entry['handle'] ?? $slug),
                'enabled' => $enabled,
                'model' => $model,
                'model_name' => is_string($modelNames[$model] ?? null) ? $modelNames[$model] : null,
                'state' => $enabled ? ($state['state'] ?? 'idle') : 'disabled',
                'state_since' => $enabled ? ($state['state_since'] ?? null) : null,
                'current_task' => $enabled ? ($state['current_task'] ?? null) : null,
                'today' => [
                    'tokens' => $used['tokens_in'] + $used['tokens_out'],
                    'tokens_in' => $used['tokens_in'],
                    'tokens_out' => $used['tokens_out'],
                    'turns' => $used['turns'],
                    'runs' => $used['runs'],
                    'cost_usd_notional' => $used['cost'],
                ],
                'budget' => [
                    'daily_tokens' => self::int($values[SettingsSchema::agentField($slug, 'daily_tokens')] ?? 0),
                    'daily_turns' => self::int($values[SettingsSchema::agentField($slug, 'daily_turns')] ?? 0),
                ],
                'prompt' => $prompts[$slug] ?? null,
                'last_run' => $run,
            ];
        }

        $tz = self::timezone();

        return [
            'data' => $agents,
            'meta' => [
                'env' => $env,
                'day' => $dayStart->setTimezone($tz)->toDateString(),
                'timezone' => $tz,
                'day_start' => self::iso($dayStart),
                'day_end' => self::iso($dayEnd),
                'settings_version' => $setting->version,
            ],
        ];
    }

    /**
     * [start, end) of the owner's day containing $now, in UTC.
     *
     * @return array{0: CarbonImmutable, 1: CarbonImmutable}
     */
    public static function dayWindowUtc(CarbonImmutable $now): array
    {
        $start = $now->setTimezone(self::timezone())->startOfDay();

        return [$start->utc(), $start->addDay()->utc()];
    }

    /**
     * @param  list<string>  $slugs
     * @return array<string, array{tokens_in: int, tokens_out: int, turns: int, runs: int, cost: string}>
     */
    private function usage(string $env, array $slugs, CarbonImmutable $start, CarbonImmutable $end): array
    {
        $rows = DB::table('agent_runs')
            ->selectRaw('agent, coalesce(sum(tokens_in), 0) as tokens_in, coalesce(sum(tokens_out), 0) as tokens_out, '
                .'coalesce(sum(turns), 0) as turns, count(*) as runs, '
                .'coalesce(sum(cost_usd_notional), 0)::numeric(18,6)::text as cost')
            ->where('env', $env)
            ->whereIn('agent', $slugs)
            ->where('started_at', '>=', $start)
            ->where('started_at', '<', $end)
            ->groupBy('agent')
            ->get();

        $out = [];
        foreach ($rows as $row) {
            $out[self::str($row->agent)] = [
                'tokens_in' => self::int($row->tokens_in),
                'tokens_out' => self::int($row->tokens_out),
                'turns' => self::int($row->turns),
                'runs' => self::int($row->runs),
                'cost' => self::str($row->cost),
            ];
        }

        return $out;
    }

    /**
     * @param  list<string>  $slugs
     * @return array<string, array{state: string, state_since: string|null, current_task: string|null}>
     */
    private function states(string $env, array $slugs): array
    {
        $out = [];
        foreach (DB::table('agent_states')->where('env', $env)->whereIn('agent', $slugs)->get() as $row) {
            $out[self::str($row->agent)] = [
                'state' => self::str($row->state),
                'state_since' => self::isoOrNull($row->state_since),
                'current_task' => is_string($row->current_task) && $row->current_task !== '' ? $row->current_task : null,
            ];
        }

        return $out;
    }

    /**
     * The newest run per agent (any day), without its error text.
     *
     * @param  list<string>  $slugs
     * @return array<string, array{id: string, status: string, trigger: string, started_at: string|null, ended_at: string|null, prompt_version: int|null}>
     */
    private function lastRuns(string $env, array $slugs): array
    {
        $rows = DB::select(
            'select distinct on (agent) id, agent, status, trigger, started_at, ended_at, prompt_version
               from agent_runs
              where env = ? and agent = any(?::varchar[])
           order by agent, started_at desc, id desc',
            [$env, self::pgArray($slugs)],
        );

        $out = [];
        foreach ($rows as $row) {
            $out[self::str($row->agent)] = [
                'id' => self::str($row->id),
                'status' => self::str($row->status),
                'trigger' => self::str($row->trigger),
                'started_at' => self::isoOrNull($row->started_at),
                'ended_at' => self::isoOrNull($row->ended_at),
                'prompt_version' => is_numeric($row->prompt_version) ? (int) $row->prompt_version : null,
            ];
        }

        return $out;
    }

    /**
     * The active prompt version per agent: the one its newest run used, else the newest stored
     * (a prompt the supervisor synced but has not run yet). `sha` is the 12-hex version shown in
     * the UI (ATL-90); `sequence` is prompt_versions.version.
     *
     * @param  list<string>  $slugs
     * @param  array<string, array{prompt_version: int|null}>  $lastRuns
     * @return array<string, array{sequence: int, sha: string, sha256: string, created_at: string|null}>
     */
    private function prompts(array $slugs, array $lastRuns): array
    {
        $rows = DB::table('prompt_versions')
            ->select(['agent', 'version', 'sha256', 'created_at'])
            ->whereIn('agent', $slugs)
            ->orderBy('agent')
            ->orderByDesc('version')
            ->get();

        $out = [];
        foreach ($rows as $row) {
            $slug = self::str($row->agent);
            $version = self::int($row->version);
            $wanted = $lastRuns[$slug]['prompt_version'] ?? null;
            $entry = [
                'sequence' => $version,
                'sha' => substr(self::str($row->sha256), 0, 12),
                'sha256' => self::str($row->sha256),
                'created_at' => self::isoOrNull($row->created_at),
            ];
            if ($wanted !== null) {
                if ($version === $wanted) {
                    $out[$slug] = $entry;
                }
            } elseif (! isset($out[$slug])) {
                $out[$slug] = $entry; // rows are newest first
            }
        }

        return $out;
    }

    /**
     * @param  list<string>  $values
     */
    private static function pgArray(array $values): string
    {
        return '{'.implode(',', array_map(static fn (string $v): string => '"'.addcslashes($v, '"\\').'"', $values)).'}';
    }

    private static function timezone(): string
    {
        $tz = config('atlas_defaults.timezone.display');

        return is_string($tz) && $tz !== '' ? $tz : 'Asia/Baghdad';
    }

    private static function iso(CarbonImmutable $ts): string
    {
        return $ts->utc()->toIso8601ZuluString('microsecond');
    }

    private static function isoOrNull(mixed $value): ?string
    {
        return is_string($value) && $value !== '' ? self::iso(CarbonImmutable::parse($value)) : null;
    }

    private static function str(mixed $value): string
    {
        return is_scalar($value) ? (string) $value : '';
    }

    private static function int(mixed $value): int
    {
        return is_numeric($value) ? (int) $value : 0;
    }
}
