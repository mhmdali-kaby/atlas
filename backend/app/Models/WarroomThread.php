<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Collection;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Support\Carbon;

/**
 * A War Room thread (brief §12): the main room, one per watchlist instrument, or a private
 * side-thread with one agent. Unique per (env, kind, key).
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property 'main'|'instrument'|'agent' $kind
 * @property string $key "main", an instId such as BTC-USDT-SWAP, or an agent key such as historian
 * @property string $title
 * @property Carbon $created_at
 * @property Carbon $updated_at
 * @property-read Collection<int, WarroomMessage> $messages
 */
#[Table(name: 'warroom_threads')]
#[Fillable(['env', 'kind', 'key', 'title'])]
class WarroomThread extends Model
{
    use HasUuids;

    public const KINDS = ['main', 'instrument', 'agent'];

    public const MAIN_KEY = 'main';

    /** Keep microseconds: messages are cursor-paged on (created_at, id). */
    protected $dateFormat = 'Y-m-d H:i:s.uP';

    /**
     * @return HasMany<WarroomMessage, $this>
     */
    public function messages(): HasMany
    {
        return $this->hasMany(WarroomMessage::class, 'thread_id');
    }
}
