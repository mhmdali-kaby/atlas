<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * Progress of one historical backfill (ATL-44): one row per (env, inst_id, dataset). Written by
 * trading_core's backfill (ATL-46); Laravel only reads it for Settings → Data (ATL-48).
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property string $inst_id an instId, or a bare ccy for the currency-level rubik datasets
 * @property string $dataset e.g. candles:1H, funding, open_interest
 * @property Carbon $target_from oldest ts wanted
 * @property Carbon|null $cursor_ts oldest ts written so far
 * @property int $rows_written
 * @property 'pending'|'running'|'done'|'failed'|'paused' $status
 * @property string|null $last_error
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'backfill_jobs')]
#[Fillable(['env', 'inst_id', 'dataset', 'target_from', 'cursor_ts', 'rows_written', 'status', 'last_error'])]
class BackfillJob extends Model
{
    use HasUuids;

    public const DATASETS = [
        'candles:1m', 'candles:5m', 'candles:15m', 'candles:1H', 'candles:4H', 'candles:1D',
        'funding', 'open_interest', 'long_short_ratio', 'taker_volume',
    ];

    public const STATUSES = ['pending', 'running', 'done', 'failed', 'paused'];

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'target_from' => 'datetime',
            'cursor_ts' => 'datetime',
            'rows_written' => 'integer',
        ];
    }
}
