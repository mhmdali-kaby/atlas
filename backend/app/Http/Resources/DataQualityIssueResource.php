<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\DataQualityIssue;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * One data-quality finding for Settings → Data (ATL-48). repaired_at null = still open.
 *
 * @mixin DataQualityIssue
 */
class DataQualityIssueResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var DataQualityIssue $issue */
        $issue = $this->resource;

        return [
            'id' => $issue->id,
            'kind' => $issue->kind,
            'inst_id' => $issue->inst_id,
            'dataset' => $issue->dataset,
            'from_ts' => $issue->from_ts->utc()->toIso8601ZuluString('microsecond'),
            'to_ts' => $issue->to_ts->utc()->toIso8601ZuluString('microsecond'),
            'detected_at' => $issue->detected_at->utc()->toIso8601ZuluString('microsecond'),
            'repaired_at' => $issue->repaired_at?->utc()->toIso8601ZuluString('microsecond'),
            'detail' => $issue->detail,
        ];
    }
}
