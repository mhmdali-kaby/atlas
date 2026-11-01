<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * One owner backtest (decision #253 L, ATL-208). Written by POST /api/backtests (queued) and the
 * RunBacktest job (running → done | failed). Research only: nothing here reaches an order path.
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property string|null $user_id
 * @property array<string, mixed> $request
 * @property 'queued'|'running'|'done'|'failed' $status
 * @property array<string, mixed>|null $result
 * @property string|null $error
 * @property string|null $error_reason
 * @property Carbon|null $started_at
 * @property Carbon|null $finished_at
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'backtest_runs')]
#[Fillable(['env', 'user_id', 'request', 'status', 'result', 'error', 'error_reason', 'started_at', 'finished_at'])]
class BacktestRun extends Model
{
    use HasUuids;

    public const STATUSES = ['queued', 'running', 'done', 'failed'];

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'request' => 'array',
            'result' => 'array',
            'started_at' => 'datetime',
            'finished_at' => 'datetime',
        ];
    }
}
