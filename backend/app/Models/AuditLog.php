<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * One write in the app (brief §16). Append-only; record it with App\Support\Auditable.
 *
 * @property string $id UUID
 * @property string|null $user_id
 * @property string $action
 * @property string $subject_type
 * @property string|null $subject_id
 * @property array<string, mixed>|null $before
 * @property array<string, mixed>|null $after
 * @property string|null $ip
 * @property Carbon $created_at
 */
#[Table(name: 'audit_log')]
#[Fillable(['user_id', 'action', 'subject_type', 'subject_id', 'before', 'after', 'ip'])]
class AuditLog extends Model
{
    use HasUuids;

    public const UPDATED_AT = null;

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'before' => 'array',
            'after' => 'array',
        ];
    }
}
