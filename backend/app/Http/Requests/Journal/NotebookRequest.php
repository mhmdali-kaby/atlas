<?php

declare(strict_types=1);

namespace App\Http\Requests\Journal;

use Carbon\CarbonImmutable;

/**
 * GET /api/agents/notebook?date=YYYY-MM-DD (ATL-200): one Asia/Baghdad day, default today there.
 */
class NotebookRequest extends JournalApiRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'date' => ['sometimes', 'date_format:Y-m-d'],
        ];
    }

    public function day(): string
    {
        $date = $this->validated('date');

        return is_string($date) ? $date : CarbonImmutable::now(self::timezone())->toDateString();
    }

    public function maxNotes(): int
    {
        return self::int('notebook_max_notes', 500);
    }
}
