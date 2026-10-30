<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * One voter's vote on one proposal version (brief §7.9). Written by the agents; read-only here.
 *
 * @property string $id UUID
 * @property string $proposal_id
 * @property string $agent
 * @property 'take'|'skip'|'change' $vote
 * @property string $confidence decimal string 0..1
 * @property string $reason
 * @property string|null $fix
 * @property Carbon $created_at
 */
#[Table(name: 'votes')]
class Vote extends Model
{
    use HasUuids;

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    protected $guarded = ['*'];
}
