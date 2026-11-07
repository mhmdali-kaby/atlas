<?php

declare(strict_types=1);

namespace App\Models;

use App\Casts\PostgresTextArray;
use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Support\Carbon;

/**
 * One War Room message (brief §5 warroom_messages). `event_id` is the id of the Redis Streams
 * envelope it was persisted from (unique, so a replayed event is stored at most once); null
 * for rows written directly.
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property string $thread_id
 * @property string|null $event_id
 * @property 'agent'|'human'|'system' $author_type
 * @property string $author
 * @property string $kind
 * @property string $content_md
 * @property array<string, mixed>|null $card
 * @property string|null $reply_to
 * @property list<string> $mentions
 * @property string|null $reasoning_md
 * @property list<array<string, mixed>>|null $tool_calls
 * @property int|null $tokens_in
 * @property int|null $tokens_out
 * @property string|null $cost_usd decimal string
 * @property Carbon $created_at
 * @property Carbon $updated_at
 * @property-read WarroomThread $thread
 * @property-read WarroomMessage|null $replyTo
 */
#[Table(name: 'warroom_messages')]
#[Fillable([
    'env', 'thread_id', 'event_id', 'author_type', 'author', 'kind', 'content_md', 'card',
    'reply_to', 'mentions', 'reasoning_md', 'tool_calls', 'tokens_in', 'tokens_out', 'cost_usd',
])]
class WarroomMessage extends Model
{
    use HasUuids;

    public const AUTHOR_TYPES = ['agent', 'human', 'system'];

    /** Message kinds rendered as cards in the feed (brief §12). */
    public const KINDS = [
        'text', 'watchlist', 'research_report', 'news_alert', 'proposal', 'vote_summary',
        'risk_check', 'order', 'fill', 'position_update', 'post_mortem', 'system_alert',
    ];

    /** Keep microseconds: messages are cursor-paged on (created_at, id). */
    protected $dateFormat = 'Y-m-d H:i:s.uP';

    /** @var array<string, mixed> */
    protected $attributes = [
        'mentions' => '{}',
    ];

    /**
     * @return BelongsTo<WarroomThread, $this>
     */
    public function thread(): BelongsTo
    {
        return $this->belongsTo(WarroomThread::class, 'thread_id');
    }

    /**
     * @return BelongsTo<WarroomMessage, $this>
     */
    public function replyTo(): BelongsTo
    {
        return $this->belongsTo(self::class, 'reply_to');
    }

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'card' => 'array',
            'mentions' => PostgresTextArray::class,
            'tool_calls' => 'array',
            'tokens_in' => 'integer',
            'tokens_out' => 'integer',
            'cost_usd' => 'decimal:6',
        ];
    }
}
