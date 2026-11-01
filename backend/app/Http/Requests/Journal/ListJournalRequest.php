<?php

declare(strict_types=1);

namespace App\Http\Requests\Journal;

use Carbon\CarbonImmutable;
use Illuminate\Pagination\Cursor;
use Illuminate\Support\Str;
use Illuminate\Validation\Rule;
use Illuminate\Validation\Validator;

/**
 * GET /api/journal?inst_id=&agent=&tag[]=&outcome=&from=&to=&limit=&cursor= (ATL-200).
 *
 * `tag` is one tag or a list; an entry matches when it carries any of them. `from`/`to` are
 * Asia/Baghdad days, inclusive. `outcome` is win|loss|scratch (data.outcome or a bare tag).
 */
class ListJournalRequest extends JournalApiRequest
{
    public const OUTCOMES = ['win', 'loss', 'scratch'];

    private const NAME = 'regex:/^[A-Za-z0-9][A-Za-z0-9._:\/-]*$/';

    protected function prepareForValidation(): void
    {
        $tag = $this->input('tag');
        if (is_string($tag)) {
            $this->merge(['tag' => [$tag]]);
        }
    }

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'inst_id' => ['sometimes', 'string', 'max:64', self::NAME],
            'agent' => ['sometimes', 'string', 'max:64', self::NAME],
            'tag' => ['sometimes', 'array', 'min:1', 'max:'.self::int('max_tags', 20)],
            'tag.*' => ['required', 'string', 'max:64'],
            'outcome' => ['sometimes', 'string', Rule::in(self::OUTCOMES)],
            'from' => ['sometimes', 'date_format:Y-m-d'],
            'to' => ['sometimes', 'date_format:Y-m-d', 'after_or_equal:from'],
            'cursor' => ['sometimes', 'nullable', 'string', 'max:1024'],
            'limit' => ['sometimes', 'integer', 'min:1', 'max:'.self::int('per_page_max', 200)],
        ];
    }

    /**
     * A crafted cursor would otherwise reach Postgres as a literal and fail there (a 500).
     *
     * @return list<callable(Validator): void>
     */
    public function after(): array
    {
        return [
            function (Validator $validator): void {
                $encoded = $this->input('cursor');
                if ($validator->errors()->has('cursor') || ! is_string($encoded) || $encoded === '') {
                    return;
                }
                if (! self::validCursor($encoded)) {
                    $validator->errors()->add('cursor', __('journal.invalid_cursor'));
                }
            },
        ];
    }

    private static function validCursor(string $encoded): bool
    {
        $params = Cursor::fromEncoded($encoded)?->toArray();
        if (! is_array($params)) {
            return false;
        }
        unset($params['_pointsToNextItems']);
        $keys = array_keys($params);
        sort($keys);
        if ($keys !== ['created_at', 'id']) {
            return false;
        }
        $id = $params['id'];
        $ts = $params['created_at'];

        return is_string($id) && Str::isUuid($id)
            && is_string($ts)
            && preg_match('/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?[+-]\d{2}(:\d{2})?$/', $ts) === 1
            && CarbonImmutable::canBeCreatedFromFormat(substr($ts, 0, 19), 'Y-m-d H:i:s')
            && substr($ts, 0, 4) !== '0000';
    }

    public function instId(): ?string
    {
        return self::text($this->validated('inst_id'));
    }

    public function agent(): ?string
    {
        return self::text($this->validated('agent'));
    }

    /**
     * @return list<string>
     */
    public function tags(): array
    {
        $tags = $this->validated('tag');

        return is_array($tags) ? array_values(array_unique(array_filter($tags, 'is_string'))) : [];
    }

    public function outcome(): ?string
    {
        return self::text($this->validated('outcome'));
    }

    /** Start of the `from` Baghdad day, in UTC. */
    public function fromUtc(): ?CarbonImmutable
    {
        $from = $this->validated('from');

        return is_string($from) ? CarbonImmutable::createFromFormat('!Y-m-d', $from, self::timezone())?->utc() : null;
    }

    /** End (exclusive) of the `to` Baghdad day, in UTC. */
    public function toUtc(): ?CarbonImmutable
    {
        $to = $this->validated('to');

        return is_string($to) ? CarbonImmutable::createFromFormat('!Y-m-d', $to, self::timezone())?->addDay()->utc() : null;
    }

    public function limit(): int
    {
        $limit = $this->validated('limit');

        return is_numeric($limit) ? (int) $limit : self::int('per_page_default', 50);
    }
}
