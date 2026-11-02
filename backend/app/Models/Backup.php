<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * One `php artisan atlas:backup` run (ATL-231, decision #255 (3)). Env-agnostic.
 *
 * @property string $id UUID
 * @property string $path
 * @property int|null $bytes
 * @property Carbon $started_at
 * @property Carbon $finished_at
 * @property 'ok'|'failed' $status
 * @property string|null $error
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'backups')]
#[Fillable(['path', 'bytes', 'started_at', 'finished_at', 'status', 'error'])]
class Backup extends Model
{
    use HasUuids;

    public const OK = 'ok';

    public const FAILED = 'failed';

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'bytes' => 'integer',
            'started_at' => 'datetime',
            'finished_at' => 'datetime',
        ];
    }
}
