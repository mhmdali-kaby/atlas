<?php

declare(strict_types=1);

namespace App\Http\Controllers\Journal;

use App\Http\Controllers\Controller;
use App\Http\Requests\Journal\AgentScoresRequest;
use App\Http\Requests\Journal\JournalApiRequest;
use App\Http\Requests\Journal\NotebookRequest;
use App\Http\Resources\JournalEntryResource;
use App\Support\Settings\SettingsSchema;
use App\Support\System\ActiveEnv;
use Brick\Math\BigDecimal;
use Brick\Math\RoundingMode;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\DB;

/**
 * Brief §16 `/api/agents` scores and the shared notebook (ATL-200). agent_scores rows are written
 * by code (atlas_agents.scoring); `data` belongs to each scorer and is passed through unchanged.
 */
class AgentScoresController extends Controller
{
    public function scores(AgentScoresRequest $request): JsonResponse
    {
        $env = ActiveEnv::get();
        [$first, $last] = $request->window();

        $rows = DB::table('agent_scores')
            ->select(['agent', 'day', 'n', 'hit_rate', 'brier_score', 'data'])
            ->where('env', $env)
            ->where('day', '>=', $first)
            ->where('day', '<=', $last)
            ->orderBy('agent')
            ->orderBy('day')
            ->get();

        /** @var array<string, list<array{day: string, n: int, hit_rate: ?string, brier_score: ?string, data: object}>> $byAgent */
        $byAgent = [];
        foreach ($rows as $row) {
            $r = (array) $row;
            $byAgent[self::str($r['agent'])][] = [
                'day' => self::str($r['day']),
                'n' => is_numeric($r['n']) ? (int) $r['n'] : 0,
                'hit_rate' => is_numeric($r['hit_rate']) ? self::str($r['hit_rate']) : null,
                'brier_score' => is_numeric($r['brier_score']) ? self::str($r['brier_score']) : null,
                'data' => JournalEntryResource::json($r['data']),
            ];
        }

        // Every roster agent appears, in roster order; an agent seen only in scores is appended.
        $slugs = SettingsSchema::agentSlugs();
        foreach (array_keys($byAgent) as $slug) {
            if (! in_array($slug, $slugs, true)) {
                $slugs[] = $slug;
            }
        }

        $directory = config('atlas_defaults.warroom.agents');
        $directory = is_array($directory) ? $directory : [];

        $agents = [];
        foreach ($slugs as $slug) {
            $entry = is_array($directory[$slug] ?? null) ? $directory[$slug] : [];
            $agentRows = $byAgent[$slug] ?? [];
            $agents[] = [
                'agent' => $slug,
                'name' => is_string($entry['name'] ?? null) ? $entry['name'] : $slug,
                'summary' => self::summary($agentRows),
                'days' => $agentRows,
            ];
        }

        return response()->json([
            'data' => $agents,
            'meta' => [
                'env' => $env,
                'days' => $request->days(),
                'from' => $first,
                'to' => $last,
                'timezone' => JournalApiRequest::timezone(),
            ],
        ]);
    }

    public function notebook(NotebookRequest $request): JsonResponse
    {
        $env = ActiveEnv::get();
        $day = $request->day();

        $notes = DB::table('agent_notebook')
            ->select(['id', 'agent', 'note_md', 'data', 'created_at'])
            ->where('env', $env)
            ->where('day', $day)
            ->orderBy('created_at')
            ->orderBy('id')
            ->limit($request->maxNotes())
            ->get();

        return response()->json([
            'data' => $notes->map(static fn (object $n): array => [
                'id' => $n->id,
                'agent' => $n->agent,
                'note_md' => $n->note_md,
                'data' => JournalEntryResource::json($n->data),
                'created_at' => JournalEntryResource::iso($n->created_at),
            ])->all(),
            'meta' => ['env' => $env, 'date' => $day, 'timezone' => JournalApiRequest::timezone()],
        ]);
    }

    /**
     * n = the window's total; hit_rate = Σ(hit_rate × n) / Σ n over days with a hit rate and n > 0;
     * brier_score = the mean of the days that have one; reliability_weight = the newest day's
     * data.reliability_weight that is numeric.
     *
     * @param  list<array{day: string, n: int, hit_rate: ?string, brier_score: ?string, data: object}>  $rows  ordered by day ascending
     * @return array<string, mixed>
     */
    private static function summary(array $rows): array
    {
        $n = 0;
        $weighted = BigDecimal::zero();
        $weight = 0;
        $brierSum = BigDecimal::zero();
        $brierCount = 0;
        $reliability = null;
        $reliabilityDay = null;

        foreach ($rows as $row) {
            $n += $row['n'];
            if ($row['hit_rate'] !== null && $row['n'] > 0) {
                $weighted = $weighted->plus(BigDecimal::of($row['hit_rate'])->multipliedBy($row['n']));
                $weight += $row['n'];
            }
            if ($row['brier_score'] !== null) {
                $brierSum = $brierSum->plus(BigDecimal::of($row['brier_score']));
                $brierCount++;
            }
            $value = $row['data']->reliability_weight ?? null;
            if ((is_string($value) || is_int($value) || is_float($value)) && is_numeric($value)) {
                $reliability = (string) $value;
                $reliabilityDay = $row['day'];
            }
        }

        $hitScale = self::scale('hit_rate_scale', 4);
        $brierScale = self::scale('brier_scale', 6);

        return [
            'n' => $n,
            'days_scored' => count($rows),
            'hit_rate' => $weight > 0
                ? (string) $weighted->dividedBy($weight, $hitScale, RoundingMode::HalfUp)
                : null,
            'brier_score' => $brierCount > 0
                ? (string) $brierSum->dividedBy($brierCount, $brierScale, RoundingMode::HalfUp)
                : null,
            'reliability_weight' => $reliability,
            'reliability_weight_day' => $reliabilityDay,
        ];
    }

    /**
     * @return int<0, max>
     */
    private static function scale(string $key, int $default): int
    {
        $value = config('atlas_defaults.journal_api.'.$key);

        return max(0, is_int($value) ? $value : $default);
    }

    private static function str(mixed $value): string
    {
        return is_scalar($value) ? (string) $value : '';
    }
}
