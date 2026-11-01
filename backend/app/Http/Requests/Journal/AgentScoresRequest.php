<?php

declare(strict_types=1);

namespace App\Http\Requests\Journal;

use Carbon\CarbonImmutable;
use Illuminate\Validation\Rule;

/**
 * GET /api/agents/scores?days=7|30|90 (ATL-200): the last `days` Asia/Baghdad days ending today.
 */
class AgentScoresRequest extends JournalApiRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'days' => ['sometimes', 'integer', Rule::in(self::allowedDays())],
        ];
    }

    /**
     * @return list<int>
     */
    public static function allowedDays(): array
    {
        $days = config('atlas_defaults.journal_api.score_days');

        return is_array($days) ? array_values(array_filter($days, 'is_int')) : [7, 30, 90];
    }

    public function days(): int
    {
        $days = $this->validated('days');

        return is_numeric($days) ? (int) $days : self::int('score_days_default', 30);
    }

    /**
     * Inclusive [first, last] Baghdad days of the window.
     *
     * @return array{0: string, 1: string}
     */
    public function window(?CarbonImmutable $now = null): array
    {
        $today = ($now ?? CarbonImmutable::now())->setTimezone(self::timezone())->startOfDay();

        return [$today->subDays($this->days() - 1)->toDateString(), $today->toDateString()];
    }
}
