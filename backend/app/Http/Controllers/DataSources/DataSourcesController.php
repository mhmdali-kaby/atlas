<?php

declare(strict_types=1);

namespace App\Http\Controllers\DataSources;

use App\Http\Controllers\Controller;
use App\Http\Requests\DataSources\SetDataSourceKeyRequest;
use App\Http\Requests\DataSources\UpdateDataSourceRequest;
use App\Http\Resources\NewsSourceResource;
use App\Models\NewsSource;
use App\Support\Auditable;
use App\Support\DataSources\SourceStatus;
use Carbon\CarbonImmutable;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Response;
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

    public function index(): JsonResponse
    {
        $sources = NewsSource::query()->orderBy('category')->orderBy('name')->get();

        return NewsSourceResource::collection($sources)
            ->additional(['summary' => SourceStatus::summary($sources, CarbonImmutable::now())])
            ->response();
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

        return new NewsSourceResource($source->refresh());
    }

    public function setKey(SetDataSourceKeyRequest $request, NewsSource $source): NewsSourceResource
    {
        $this->ensureKeyed($source);
        $apiKey = $request->apiKey();

        DB::transaction(function () use ($source, $apiKey): void {
            $hadKey = $source->getRawOriginal('api_key') !== null;
            $source->forceFill([
                'api_key' => $apiKey,
                // A new key gets a fresh start: the old key's failures no longer apply.
                'consecutive_failures' => 0,
                'disabled_until' => null,
                'last_error' => null,
            ])->save();
            $this->audit->record(
                action: 'news_source.key_set',
                subjectType: self::SUBJECT,
                subjectId: $source->id,
                before: ['source' => $source->key, 'api_key' => $hadKey ? 'key_set' : null],
                after: ['source' => $source->key, 'api_key' => 'key_set'],
            );
        });

        return new NewsSourceResource($source->refresh());
    }

    public function removeKey(NewsSource $source): Response
    {
        $this->ensureKeyed($source);

        DB::transaction(function () use ($source): void {
            if ($source->getRawOriginal('api_key') === null) {
                return;
            }
            $source->forceFill(['api_key' => null])->save();
            $this->audit->record(
                action: 'news_source.key_removed',
                subjectType: self::SUBJECT,
                subjectId: $source->id,
                before: ['source' => $source->key, 'api_key' => 'key_set'],
                after: ['source' => $source->key, 'api_key' => 'key_removed'],
            );
        });

        return response()->noContent();
    }

    private function ensureKeyed(NewsSource $source): void
    {
        if (! $source->requires_key) {
            throw ValidationException::withMessages(['api_key' => __('data_sources.key_not_used')]);
        }
    }
}
