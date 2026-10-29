<?php

declare(strict_types=1);

namespace App\Http\Controllers\DataSources;

use App\Http\Controllers\Controller;
use App\Http\Requests\DataSources\SetDataSourceKeyRequest;
use App\Http\Requests\DataSources\UpdateDataSourceRequest;
use App\Http\Resources\NewsSourceResource;
use App\Models\NewsSource;
use App\Support\Auditable;
use App\Support\DataSources\HourlyCounts;
use App\Support\DataSources\SourceCoverage;
use App\Support\DataSources\SourceStatus;
use Carbon\CarbonImmutable;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Collection;
use Illuminate\Support\Facades\Crypt;
use Illuminate\Support\Facades\DB;
use Illuminate\Validation\ValidationException;

/**
 * Settings → Data sources (brief §7.2, §14.10; decision #243; ATL-66). Lists source health,
 * toggles a source, sets its poll interval, and sets or removes its API key. Every write is
 * audited inside its transaction; the key itself never reaches a response, log or audit row
 * (audit says `key_set` / `key_removed` only).
 */
class DataSourcesController extends Controller
{
    private const SUBJECT = 'news_sources';

    public function __construct(private readonly Auditable $audit) {}

    public function index(Request $request): JsonResponse
    {
        $sources = NewsSource::query()->orderBy('category')->orderBy('name')->get();
        $now = CarbonImmutable::now();

        return response()->json([
            'data' => $this->present($sources, $sources, $now)
                ->map(fn (NewsSourceResource $r): array => $r->resolve($request))
                ->all(),
            'summary' => SourceStatus::summary($sources, $now),
        ]);
    }

    public function update(UpdateDataSourceRequest $request, NewsSource $source): NewsSourceResource
    {
        /** @var array{enabled?: bool, poll_seconds?: int} $changes */
        $changes = $request->safe()->only(['enabled', 'poll_seconds']);

        DB::transaction(function () use ($source, $changes): void {
            $before = $source->only(['enabled', 'poll_seconds']);
            $source->fill($changes);
            if (! $source->isDirty()) {
                return;
            }
            $source->save();
            $this->audit->record(
                action: 'news_source.updated',
                subjectType: self::SUBJECT,
                subjectId: $source->id,
                before: ['source' => $source->key, ...$before],
                after: ['source' => $source->key, ...$source->only(['enabled', 'poll_seconds'])],
            );
        });

        return $this->one($source);
    }

    /**
     * The key belongs to the provider (ATL-71): one ciphertext is written to every keyed source of
     * $source's provider, and their failure columns are reset, in one transaction with one audit row.
     */
    public function setKey(SetDataSourceKeyRequest $request, NewsSource $source): NewsSourceResource
    {
        $this->ensureKeyed($source);
        $ciphertext = Crypt::encryptString($request->apiKey());

        DB::transaction(function () use ($source, $ciphertext): void {
            $siblings = $this->providerSources($source);
            $hadKey = $siblings->filter(fn (NewsSource $s): bool => $s->getRawOriginal('api_key') !== null);
            $keys = $siblings->pluck('key')->values()->all();

            NewsSource::query()->whereIn('id', $siblings->pluck('id'))->update([
                'api_key' => $ciphertext,
                // A new key gets a fresh start: the old key's failures no longer apply.
                'consecutive_failures' => 0,
                'disabled_until' => null,
                'last_error' => null,
                'updated_at' => CarbonImmutable::now(),
            ]);
            $this->audit->record(
                action: 'news_source.key_set',
                subjectType: self::SUBJECT,
                subjectId: $source->id,
                before: [
                    'source' => $source->key,
                    'provider' => $source->provider,
                    'sources' => $keys,
                    'api_key' => $hadKey->isEmpty() ? null : 'key_set',
                ],
                after: ['source' => $source->key, 'provider' => $source->provider, 'sources' => $keys, 'api_key' => 'key_set'],
            );
        });

        return $this->one($source->refresh());
    }

    public function removeKey(NewsSource $source): Response
    {
        $this->ensureKeyed($source);

        DB::transaction(function () use ($source): void {
            $keyed = $this->providerSources($source)
                ->filter(fn (NewsSource $s): bool => $s->getRawOriginal('api_key') !== null);
            if ($keyed->isEmpty()) {
                return;
            }
            $keys = $keyed->pluck('key')->values()->all();
            NewsSource::query()->whereIn('id', $keyed->pluck('id'))->update([
                'api_key' => null,
                'updated_at' => CarbonImmutable::now(),
            ]);
            $this->audit->record(
                action: 'news_source.key_removed',
                subjectType: self::SUBJECT,
                subjectId: $source->id,
                before: ['source' => $source->key, 'provider' => $source->provider, 'sources' => $keys, 'api_key' => 'key_set'],
                after: ['source' => $source->key, 'provider' => $source->provider, 'sources' => $keys, 'api_key' => 'key_removed'],
            );
        });

        return response()->noContent();
    }

    /**
     * Every source sharing $source's provider that takes a key, locked for the write.
     *
     * @return Collection<int, NewsSource>
     */
    private function providerSources(NewsSource $source): Collection
    {
        return NewsSource::query()
            ->where('provider', $source->provider)
            ->where('requires_key', true)
            ->orderBy('key')
            ->lockForUpdate()
            ->get();
    }

    private function one(NewsSource $source): NewsSourceResource
    {
        $all = NewsSource::query()->get();

        /** @var NewsSourceResource */
        return $this->present(collect([$source]), $all, CarbonImmutable::now())->first();
    }

    /**
     * @param  Collection<int, NewsSource>  $sources  rows to render
     * @param  Collection<int, NewsSource>  $all  every source, for covered_by
     * @return Collection<int, NewsSourceResource>
     */
    private function present(Collection $sources, Collection $all, CarbonImmutable $now): Collection
    {
        /** @var list<string> $keys */
        $keys = $sources->pluck('key')->values()->all();
        $hourly = HourlyCounts::forSources($keys, $now);
        $coveredBy = SourceCoverage::coveredBy($all);

        return $sources->values()->map(
            fn (NewsSource $s): NewsSourceResource => (new NewsSourceResource($s))
                ->withStats($hourly[$s->key], $coveredBy[$s->key] ?? null),
        );
    }

    private function ensureKeyed(NewsSource $source): void
    {
        if (! $source->requires_key) {
            throw ValidationException::withMessages(['api_key' => __('data_sources.key_not_used')]);
        }
    }
}
