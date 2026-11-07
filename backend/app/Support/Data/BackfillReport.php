<?php

declare(strict_types=1);

namespace App\Support\Data;

use App\Http\Resources\DataQualityIssueResource;
use App\Models\BackfillJob;
use App\Models\DataQualityIssue;
use Brick\Math\BigDecimal;
use Brick\Math\RoundingMode;
use Carbon\CarbonImmutable;
use Illuminate\Database\Eloquent\Collection;
use Illuminate\Http\Resources\Json\AnonymousResourceCollection;
use Illuminate\Support\Facades\DB;

/**
 * Builds the Settings → Data backfill report (ATL-48) from backfill_jobs, instruments and
 * data_quality_issues. Postgres only; never calls OKX or trading_core.
 *
 * expected/percent rules:
 *  - A dataset whose configured depth is null (full history: funding, OI, candles:1D) has no
 *    expected row count and no percent until the job is done; then expected = rows_written and
 *    percent = 100. Never an estimate.
 *  - A candle dataset with a depth: expected = whole bars between max(target_from, instrument
 *    list_time) and the job's created_at; percent = rows_written / expected × 100, capped at 100.
 *  - Any other dataset with a depth: expected null; percent = how far cursor_ts has walked back
 *    from created_at towards target_from.
 *  - A done job is always "100.0". Percents are decimal strings with one decimal, like Portfolio.
 */
final class BackfillReport
{
    /** Bar length in seconds per candle dataset. */
    private const BAR_SECONDS = [
        'candles:1m' => 60,
        'candles:5m' => 300,
        'candles:15m' => 900,
        'candles:1H' => 3600,
        'candles:4H' => 14400,
        'candles:1D' => 86400,
    ];

    private const FULL = '100.0';

    public function __construct(private readonly CarbonImmutable $now) {}

    /**
     * @param  'demo'|'live'  $env
     * @return array<string, mixed>
     */
    public function build(string $env): array
    {
        /** @var Collection<int, BackfillJob> $jobs */
        $jobs = BackfillJob::query()->where('env', $env)->orderBy('inst_id')->get();

        $instIds = $jobs->pluck('inst_id')->unique()->values()->all();
        /** @var array<string, object{inst_id: string, asset_class: string, list_time: string|null}> $instruments */
        $instruments = DB::table('instruments')
            ->where('env', $env)
            ->whereIn('inst_id', $instIds)
            ->get(['inst_id', 'asset_class', 'list_time'])
            ->keyBy('inst_id')
            ->all();

        $openByInst = DataQualityIssue::query()
            ->where('env', $env)
            ->whereNull('repaired_at')
            ->selectRaw('inst_id, count(*) as n')
            ->groupBy('inst_id')
            ->pluck('n', 'inst_id')
            ->map(fn (mixed $n): int => (int) $n)
            ->all();

        $order = array_flip(BackfillJob::DATASETS);
        $rows = [];
        foreach ($jobs->groupBy('inst_id') as $instId => $instJobs) {
            $instrument = $instruments[$instId] ?? null;
            $listTime = $instrument?->list_time !== null ? CarbonImmutable::parse($instrument->list_time) : null;
            $datasets = $instJobs
                ->sortBy(fn (BackfillJob $job): int => $order[$job->dataset] ?? PHP_INT_MAX)
                ->map(fn (BackfillJob $job): array => $this->dataset($job, $listTime))
                ->values()
                ->all();
            $rows[] = [
                'inst_id' => (string) $instId,
                'asset_class' => $instrument?->asset_class,
                'open_issues' => $openByInst[$instId] ?? 0,
                'datasets' => $datasets,
            ];
        }

        $byStatus = array_fill_keys(BackfillJob::STATUSES, 0);
        foreach ($jobs as $job) {
            $byStatus[$job->status]++;
        }

        $issues = $this->issues($env);

        return [
            'env' => $env,
            'as_of' => $this->now->utc()->toIso8601ZuluString('microsecond'),
            'instruments' => $rows,
            'totals' => [
                'instruments' => count($rows),
                'jobs' => $jobs->count(),
                'by_status' => $byStatus,
                'rows_written' => (int) $jobs->sum('rows_written'),
                'percent' => $jobs->count() === 0 ? null : $this->percent($byStatus['done'], $jobs->count()),
            ],
            'quality' => $this->qualityCounts($env),
            'quality_issues' => $issues,
        ];
    }

    /**
     * @return array<string, mixed>
     */
    private function dataset(BackfillJob $job, ?CarbonImmutable $listTime): array
    {
        $depth = self::depthDays($job->dataset);
        $done = $job->status === 'done';
        $created = CarbonImmutable::instance($job->created_at);
        $targetFrom = CarbonImmutable::instance($job->target_from);

        $expected = null;
        $percent = null;
        if ($depth === null) {
            if ($done) {
                $expected = $job->rows_written;
                $percent = self::FULL;
            }
        } elseif (isset(self::BAR_SECONDS[$job->dataset])) {
            $from = $listTime !== null && $listTime->greaterThan($targetFrom) ? $listTime : $targetFrom;
            $span = $created->getTimestamp() - $from->getTimestamp();
            $expected = max(0, intdiv($span, self::BAR_SECONDS[$job->dataset]));
            $percent = $done ? self::FULL : $this->percent($job->rows_written, $expected);
        } elseif ($done) {
            $percent = self::FULL;
        } else {
            $whole = $created->getTimestamp() - $targetFrom->getTimestamp();
            $walked = $job->cursor_ts === null ? 0 : $created->getTimestamp() - $job->cursor_ts->getTimestamp();
            $percent = $this->percent(max(0, $walked), $whole);
        }

        return [
            'dataset' => $job->dataset,
            'status' => $job->status,
            'depth_days' => $depth,
            'target_from' => $depth === null ? null : $targetFrom->utc()->toIso8601ZuluString('microsecond'),
            'cursor_ts' => $job->cursor_ts?->utc()->toIso8601ZuluString('microsecond'),
            'rows_written' => $job->rows_written,
            'expected' => $expected,
            'percent' => $percent,
            'last_error' => $job->last_error,
            'updated_at' => $job->updated_at->utc()->toIso8601ZuluString('microsecond'),
        ];
    }

    /**
     * Configured depth for a dataset in days; null means full history.
     */
    public static function depthDays(string $dataset): ?int
    {
        $value = config("atlas_defaults.backfill.depth_days.{$dataset}");

        return is_int($value) ? $value : null;
    }

    /**
     * Open issues plus those repaired within the configured window, newest detection first.
     *
     * Serialised by DataQualityIssueResource when the response is encoded.
     */
    private function issues(string $env): AnonymousResourceCollection
    {
        $days = config('atlas_defaults.data_api.repaired_issue_days');
        $limit = config('atlas_defaults.data_api.quality_issue_limit');
        $since = $this->now->subDays(is_int($days) ? $days : 7);

        return DataQualityIssueResource::collection(DataQualityIssue::query()
            ->where('env', $env)
            ->where(fn ($q) => $q->whereNull('repaired_at')->orWhere('repaired_at', '>=', $since))
            ->orderByDesc('detected_at')
            ->orderBy('id')
            ->limit(is_int($limit) ? $limit : 500)
            ->get());
    }

    /**
     * @return array{open: int, open_by_kind: array<string, int>}
     */
    private function qualityCounts(string $env): array
    {
        $byKind = array_fill_keys(DataQualityIssue::KINDS, 0);
        $counts = DataQualityIssue::query()
            ->where('env', $env)
            ->whereNull('repaired_at')
            ->selectRaw('kind, count(*) as n')
            ->groupBy('kind')
            ->pluck('n', 'kind');
        foreach ($counts as $kind => $n) {
            $byKind[(string) $kind] = (int) $n;
        }

        return ['open' => array_sum($byKind), 'open_by_kind' => $byKind];
    }

    /**
     * part / whole × 100 as a decimal string with one decimal (half-up), capped at "100.0".
     * A whole of zero or less means there is nothing to fetch, so it is complete.
     */
    private function percent(int $part, int $whole): string
    {
        if ($whole <= 0) {
            return self::FULL;
        }
        $pct = BigDecimal::of($part)->multipliedBy(100)->dividedBy($whole, 1, RoundingMode::HalfUp);

        return $pct->isGreaterThan(100) ? self::FULL : (string) $pct;
    }
}
