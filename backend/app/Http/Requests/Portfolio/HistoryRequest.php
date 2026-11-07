<?php

declare(strict_types=1);

namespace App\Http\Requests\Portfolio;

use App\Http\Requests\Settings\SettingsEnvRequest;
use App\Support\Portfolio\HistoryFilters;
use App\Support\Portfolio\HistoryQuery;
use Carbon\CarbonImmutable;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Pagination\Cursor;
use Illuminate\Support\Str;
use Illuminate\Validation\Rule;
use Illuminate\Validation\Validator;

/**
 * GET /api/portfolio/history?type=fills|closed and GET /api/exports/{fills|closed}.csv (ATL-172).
 *
 * Filters: env, inst_id, asset_class, agent, strategy_tag (closed only), from/to (Asia/Baghdad days,
 * inclusive, Y-m-d), side (fills: buy|sell; closed: long|short, or buy|sell meaning the opening side),
 * win_loss (win|loss), and for the JSON list cursor + limit. The export takes the type from the path.
 */
class HistoryRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        $max = config('atlas_defaults.portfolio_history.per_page_max');

        return [
            'type' => ['sometimes', 'string', Rule::in(HistoryQuery::TYPES)],
            'env' => ['sometimes', 'string', Rule::in(SettingsEnvRequest::envs())],
            'inst_id' => ['sometimes', 'string', 'max:64'],
            'asset_class' => ['sometimes', 'string', Rule::in(['crypto', 'stock', 'etf', 'index', 'commodity', 'other'])],
            'agent' => ['sometimes', 'string', 'max:64'],
            'strategy_tag' => ['sometimes', 'string', 'max:64'],
            'from' => ['sometimes', 'date_format:Y-m-d'],
            'to' => ['sometimes', 'date_format:Y-m-d', 'after_or_equal:from'],
            'side' => ['sometimes', 'string', Rule::in(['buy', 'sell', 'long', 'short'])],
            'win_loss' => ['sometimes', 'string', Rule::in(['win', 'loss'])],
            'cursor' => ['sometimes', 'nullable', 'string', 'max:1024'],
            'limit' => ['sometimes', 'integer', 'min:1', 'max:'.(is_numeric($max) ? (int) $max : 200)],
        ];
    }

    /**
     * Rules that depend on the type, and a cursor that would otherwise fail inside the paginator
     * (a 500) is a 422 here.
     *
     * @return list<callable(Validator): void>
     */
    public function after(): array
    {
        return [
            function (Validator $validator): void {
                if ($validator->errors()->isNotEmpty()) {
                    return;
                }
                $type = $this->type();
                if ($type === HistoryQuery::FILLS && $this->filled('strategy_tag')) {
                    $validator->errors()->add('strategy_tag', __('portfolio_history.strategy_tag_closed_only'));
                }
                if ($type === HistoryQuery::FILLS && in_array($this->input('side'), ['long', 'short'], true)) {
                    $validator->errors()->add('side', __('portfolio_history.fill_side'));
                }
                $encoded = $this->input('cursor');
                if (is_string($encoded) && $encoded !== '' && ! self::validCursor($encoded, HistoryQuery::timeColumn($type))) {
                    $validator->errors()->add('cursor', __('portfolio_history.invalid_cursor'));
                }
            },
        ];
    }

    /**
     * The cursor carries exactly the two sort keys: the timestamptz as Postgres returned it
     * (microseconds and offset kept, so paging never drops rows sharing a second) and the row's UUID.
     */
    private static function validCursor(string $encoded, string $timeColumn): bool
    {
        $params = Cursor::fromEncoded($encoded)?->toArray();
        if (! is_array($params)) {
            return false;
        }
        unset($params['_pointsToNextItems']);
        $keys = array_keys($params);
        sort($keys);
        $expected = [$timeColumn, 'id'];
        sort($expected);
        if ($keys !== $expected) {
            return false;
        }
        $id = $params['id'];
        $ts = $params[$timeColumn];

        return is_string($id) && Str::isUuid($id)
            && is_string($ts)
            && preg_match('/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?[+-]\d{2}(:\d{2})?$/', $ts) === 1
            && CarbonImmutable::canBeCreatedFromFormat(substr($ts, 0, 19), 'Y-m-d H:i:s')
            && substr($ts, 0, 4) !== '0000';
    }

    /** Set by the export route from the path; the list endpoint reads ?type= (default fills). */
    public function type(): string
    {
        $type = $this->route('type') ?? $this->input('type');

        return $type === HistoryQuery::CLOSED ? HistoryQuery::CLOSED : HistoryQuery::FILLS;
    }

    public function filters(): HistoryFilters
    {
        $env = $this->validated('env') ?? config('atlas_defaults.env.default');
        $tz = config('atlas_defaults.timezone.display');
        $tz = is_string($tz) ? $tz : 'Asia/Baghdad';
        $from = $this->validated('from');
        $to = $this->validated('to');

        return new HistoryFilters(
            type: $this->type(),
            env: is_string($env) ? $env : 'demo',
            instId: self::text($this->validated('inst_id')),
            assetClass: self::text($this->validated('asset_class')),
            agent: self::text($this->validated('agent')),
            strategyTag: self::text($this->validated('strategy_tag')),
            fromUtc: is_string($from) ? CarbonImmutable::createFromFormat('!Y-m-d', $from, $tz)?->utc() : null,
            toUtc: is_string($to) ? CarbonImmutable::createFromFormat('!Y-m-d', $to, $tz)?->addDay()->utc() : null,
            side: self::text($this->validated('side')),
            winLoss: self::text($this->validated('win_loss')),
        );
    }

    public function limit(): int
    {
        $limit = $this->validated('limit');
        $default = config('atlas_defaults.portfolio_history.per_page_default');

        return is_numeric($limit) ? (int) $limit : (is_numeric($default) ? (int) $default : 50);
    }

    private static function text(mixed $value): ?string
    {
        return is_string($value) && $value !== '' ? $value : null;
    }
}
