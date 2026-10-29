<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * A data-quality finding (ATL-44, brief §7.3): a gap, duplicate, zero-volume bar or outlier in one
 * dataset of one instrument. Open while repaired_at is null. Written by trading_core (ATL-47).
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property string $inst_id
 * @property string $dataset
 * @property 'gap'|'duplicate'|'zero_volume'|'outlier' $kind
 * @property Carbon $from_ts
 * @property Carbon $to_ts
 * @property array<string, mixed>|null $detail
 * @property Carbon $detected_at
 * @property Carbon|null $repaired_at
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'data_quality_issues')]
#[Fillable(['env', 'inst_id', 'dataset', 'kind', 'from_ts', 'to_ts', 'detail', 'detected_at', 'repaired_at'])]
class DataQualityIssue extends Model
{
    use HasUuids;

    public const KINDS = ['gap', 'duplicate', 'zero_volume', 'outlier'];

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'from_ts' => 'datetime',
            'to_ts' => 'datetime',
            'detail' => 'array',
            'detected_at' => 'datetime',
            'repaired_at' => 'datetime',
        ];
    }
}
