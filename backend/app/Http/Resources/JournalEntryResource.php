<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Casts\PostgresTextArray;
use Carbon\CarbonImmutable;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * One journal_entries row (ATL-200), read with the query builder so the cursor keeps Postgres'
 * microsecond timestamps. `data` is passed through as the writer stored it.
 */
class JournalEntryResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        $row = (array) $this->resource;

        return [
            'id' => $row['id'],
            'env' => $row['env'],
            'title' => $row['title'],
            'inst_id' => $row['inst_id'],
            'agent' => $row['agent'],
            'proposal_id' => $row['proposal_id'],
            'expected_md' => $row['expected_md'],
            'actual_md' => $row['actual_md'],
            'lessons_md' => $row['lessons_md'],
            'tags' => PostgresTextArray::parse(is_string($row['tags']) ? $row['tags'] : '{}'),
            'data' => self::json($row['data']),
            'created_at' => self::iso($row['created_at']),
        ];
    }

    /**
     * A jsonb object as a JSON object: an empty one stays `{}` rather than becoming `[]`.
     */
    public static function json(mixed $value): object
    {
        $decoded = is_string($value) ? json_decode($value, false) : null;

        return is_object($decoded) ? $decoded : new \stdClass;
    }

    public static function iso(mixed $value): ?string
    {
        return is_string($value)
            ? CarbonImmutable::parse($value)->utc()->toIso8601ZuluString('microsecond')
            : null;
    }
}
