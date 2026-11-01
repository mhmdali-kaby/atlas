<?php

declare(strict_types=1);

namespace App\Http\Requests\Journal;

use Illuminate\Validation\Validator;

/**
 * GET /api/journal/weekly-reviews?limit=&cursor= (ATL-200): the Historian's weekly reviews.
 */
class ListWeeklyReviewsRequest extends JournalApiRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'cursor' => ['sometimes', 'nullable', 'string', 'max:1024'],
            'limit' => ['sometimes', 'integer', 'min:1', 'max:'.self::int('per_page_max', 200)],
        ];
    }

    /**
     * @return list<callable(Validator): void>
     */
    public function after(): array
    {
        return [$this->cursorRule()];
    }
}
