<?php

declare(strict_types=1);

namespace App\Http\Requests\News;

use App\Rules\IsoDateTime;
use Carbon\CarbonImmutable;
use Illuminate\Validation\Rule;
use Illuminate\Validation\Validator;

/**
 * GET /api/calendar?from=&to=&min_importance=&kind=economic|earnings|all&limit=
 * Default window: now .. now + news.api.calendar_default_hours (48 h).
 */
class CalendarRequest extends NewsApiRequest
{
    public const KINDS = ['economic', 'earnings', 'all'];

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'from' => ['sometimes', 'string', new IsoDateTime],
            'to' => ['sometimes', 'string', new IsoDateTime],
            'min_importance' => ['sometimes', 'integer', 'min:1', 'max:5'],
            'kind' => ['sometimes', 'string', Rule::in(self::KINDS)],
            'limit' => self::limitRule(),
        ];
    }

    /**
     * @return list<callable(Validator): void>
     */
    public function after(): array
    {
        return [
            function (Validator $validator): void {
                if ($validator->errors()->hasAny(['from', 'to'])) {
                    return;
                }
                if ($this->to()->lessThan($this->from())) {
                    $validator->errors()->add('to', __('news.range_reversed', ['attribute' => 'to']));
                }
            },
        ];
    }

    public function from(): CarbonImmutable
    {
        return IsoDateTime::parse($this->input('from')) ?? $this->now();
    }

    public function to(): CarbonImmutable
    {
        return IsoDateTime::parse($this->input('to'))
            ?? $this->from()->addHours(self::intDefault('calendar_default_hours', 48));
    }

    public function minImportance(): ?int
    {
        $value = $this->validated('min_importance');

        return is_numeric($value) ? (int) $value : null;
    }

    /**
     * @return 'economic'|'earnings'|'all'
     */
    public function kind(): string
    {
        return match ($this->validated('kind')) {
            'economic' => 'economic',
            'earnings' => 'earnings',
            default => 'all',
        };
    }

    public function limit(): int
    {
        $limit = $this->validated('limit');

        return is_numeric($limit) ? (int) $limit : self::intDefault('page_limit_max', 100);
    }

    private ?CarbonImmutable $now = null;

    private function now(): CarbonImmutable
    {
        return $this->now ??= CarbonImmutable::now('UTC');
    }
}
