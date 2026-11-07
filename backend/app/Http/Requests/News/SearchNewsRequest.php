<?php

declare(strict_types=1);

namespace App\Http\Requests\News;

use App\Rules\IsoDateTime;
use App\Support\News\NewsSearch;
use Carbon\CarbonImmutable;
use Illuminate\Pagination\Cursor;
use Illuminate\Support\Str;
use Illuminate\Validation\Rule;
use Illuminate\Validation\Validator;

/**
 * GET /api/news?q=&inst_ids[]=&asset_class=&event_type=&min_importance=&since=&until=&cluster=1
 *     &limit=&cursor=
 */
class SearchNewsRequest extends NewsApiRequest
{
    /** Postgres' timestamptz text output, which is what the cursor carries for published_at. */
    private const CURSOR_TS_PATTERN = '/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)([+-]\d{2})(?::?(\d{2}))?$/';

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'q' => ['sometimes', 'nullable', 'string', 'max:'.NewsSearch::MAX_QUERY_LENGTH],
            'inst_ids' => ['sometimes', 'array', 'max:'.self::intDefault('max_inst_ids', 50)],
            'inst_ids.*' => ['required', 'string', 'max:64', 'regex:/^[A-Za-z0-9][A-Za-z0-9._-]*$/'],
            'asset_class' => ['sometimes', 'string', Rule::in(NewsSearch::ASSET_CLASSES)],
            'event_type' => ['sometimes', 'string', Rule::in(NewsSearch::EVENT_TYPES)],
            'min_importance' => ['sometimes', 'integer', 'min:1', 'max:5'],
            'since' => ['sometimes', 'string', new IsoDateTime],
            'until' => ['sometimes', 'string', new IsoDateTime],
            'cluster' => ['sometimes', 'boolean'],
            'limit' => self::limitRule(),
            'cursor' => ['sometimes', 'nullable', 'string', 'max:1024'],
        ];
    }

    /**
     * A crafted cursor would otherwise reach Postgres as a literal and fail there (a 500); the
     * paginator orders by published_at then id, so both must be present and well-formed.
     *
     * @return list<callable(Validator): void>
     */
    public function after(): array
    {
        return [
            function (Validator $validator): void {
                $since = $this->has('since') && ! $validator->errors()->has('since') ? IsoDateTime::parse($this->input('since')) : null;
                $until = $this->has('until') && ! $validator->errors()->has('until') ? IsoDateTime::parse($this->input('until')) : null;
                if ($since !== null && $until !== null && $until->lessThan($since)) {
                    $validator->errors()->add('until', __('news.range_reversed', ['attribute' => 'until']));
                }

                $encoded = $this->input('cursor');
                if (! is_string($encoded) || $validator->errors()->has('cursor')) {
                    return;
                }
                $parameters = Cursor::fromEncoded($encoded)?->toArray();
                $id = $parameters['id'] ?? null;
                $publishedAt = $parameters['published_at'] ?? null;
                if (! is_string($id) || ! Str::isUuid($id) || self::parseCursorTimestamp($publishedAt) === null) {
                    $validator->errors()->add('cursor', __('news.invalid_cursor'));
                }
            },
        ];
    }

    private static function parseCursorTimestamp(mixed $value): ?CarbonImmutable
    {
        if (! is_string($value) || preg_match(self::CURSOR_TS_PATTERN, $value, $m) !== 1) {
            return null;
        }

        return IsoDateTime::parse("{$m[1]}T{$m[2]}{$m[3]}:".(($m[4] ?? '') !== '' ? $m[4] : '00'));
    }

    public function search(): ?string
    {
        $q = $this->validated('q');

        return is_string($q) && trim($q) !== '' ? trim($q) : null;
    }

    /**
     * @return list<string>
     */
    public function instIds(): array
    {
        $ids = $this->validated('inst_ids', []);

        return is_array($ids) ? array_values(array_unique(array_map('strval', $ids))) : [];
    }

    public function assetClass(): ?string
    {
        $value = $this->validated('asset_class');

        return is_string($value) ? $value : null;
    }

    public function eventType(): ?string
    {
        $value = $this->validated('event_type');

        return is_string($value) ? $value : null;
    }

    public function minImportance(): ?int
    {
        $value = $this->validated('min_importance');

        return is_numeric($value) ? (int) $value : null;
    }

    public function since(): ?CarbonImmutable
    {
        return $this->dateInput('since');
    }

    public function until(): ?CarbonImmutable
    {
        return $this->dateInput('until');
    }

    public function clustered(): bool
    {
        return $this->boolean('cluster');
    }
}
