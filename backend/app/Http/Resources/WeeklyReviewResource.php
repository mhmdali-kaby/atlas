<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Http\Requests\Journal\JournalApiRequest;
use Carbon\CarbonImmutable;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * One weekly_review research_reports row (ATL-200). `week_start` is the Baghdad Monday of the
 * reviewed week: `data.week_start` when the writer stored a Y-m-d date there, otherwise the Monday
 * of the Baghdad week the review was written in (the review runs Sunday 18:00, the week's last day).
 * `data` is passed through unchanged.
 */
class WeeklyReviewResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        $row = (array) $this->resource;
        $data = JournalEntryResource::json($row['data']);
        $weekStart = self::weekStart($data, $row['created_at']);

        return [
            'id' => $row['id'],
            'env' => $row['env'],
            'agent' => $row['agent'],
            'week_start' => $weekStart?->toDateString(),
            'week_end' => $weekStart?->addDays(6)->toDateString(),
            'content_md' => $row['content_md'],
            'data' => $data,
            'created_at' => JournalEntryResource::iso($row['created_at']),
        ];
    }

    private static function weekStart(object $data, mixed $createdAt): ?CarbonImmutable
    {
        $stored = $data->week_start ?? null;
        if (is_string($stored) && preg_match('/^\d{4}-\d{2}-\d{2}$/', $stored) === 1
            && CarbonImmutable::canBeCreatedFromFormat($stored, 'Y-m-d')) {
            $date = CarbonImmutable::createFromFormat('!Y-m-d', $stored, JournalApiRequest::timezone());
            if ($date !== null && $date->format('Y-m-d') === $stored) {
                return $date;
            }
        }
        if (! is_string($createdAt)) {
            return null;
        }

        return CarbonImmutable::parse($createdAt)->setTimezone(JournalApiRequest::timezone())
            ->startOfWeek(CarbonImmutable::MONDAY)->startOfDay();
    }
}
