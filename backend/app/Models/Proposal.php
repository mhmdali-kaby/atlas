<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Database\Eloquent\Relations\HasOne;
use Illuminate\Support\Carbon;

/**
 * A trade proposal or a position action (ATL-124; brief §5, §8). Written by the agents in Python;
 * the owner's approve / reject / edit (ATL-162) are the only Laravel writes. Prices and sizes are
 * numeric(38,18) and stay decimal strings (never floats). A revision is a new row with
 * version + 1 and parent_id = the row it revises.
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property string|null $cycle_id
 * @property int $version
 * @property string|null $parent_id
 * @property string $kind entry|amend_stop|amend_targets|partial_close|close
 * @property string $author
 * @property string $inst_id
 * @property 'buy'|'sell'|null $side
 * @property string|null $order_type
 * @property string|null $entry
 * @property list<mixed>|null $entry_zone decimal strings [low, high] as stored
 * @property string|null $stop
 * @property list<array<string, mixed>> $take_profits [{"price": "…", "pct": "…"}] as stored (jsonb)
 * @property int|null $time_stop_minutes
 * @property string|null $size_suggestion_pct
 * @property string|null $close_pct
 * @property int|null $leverage_suggestion
 * @property string|null $expected_r
 * @property string|null $probability
 * @property string $rationale_md
 * @property string $invalidation
 * @property list<array<string, mixed>> $evidence
 * @property string $status
 * @property Carbon|null $approval_expires_at
 * @property string|null $status_reason
 * @property string|null $owner_note
 * @property string|null $decided_by
 * @property Carbon|null $decided_at
 * @property Carbon|null $approval_reminded_at
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'proposals')]
#[Fillable([
    'env', 'cycle_id', 'version', 'parent_id', 'kind', 'author', 'inst_id', 'side', 'order_type',
    'entry', 'entry_zone', 'stop', 'take_profits', 'time_stop_minutes', 'size_suggestion_pct',
    'close_pct', 'leverage_suggestion', 'expected_r', 'probability', 'rationale_md', 'invalidation',
    'evidence', 'status', 'approval_expires_at', 'status_reason', 'owner_note', 'decided_by',
    'decided_at', 'approval_reminded_at',
])]
class Proposal extends Model
{
    use HasUuids;

    /** Brief §8 lifecycle, plus advise_only (ATL-160). */
    public const STATUSES = [
        'draft', 'under_review', 'voting', 'passed', 'rejected', 'risk_checked', 'awaiting_owner',
        'advise_only', 'sent', 'partially_filled', 'filled', 'closed', 'cancelled', 'expired',
    ];

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    /** @return BelongsTo<Proposal, $this> */
    public function parent(): BelongsTo
    {
        return $this->belongsTo(Proposal::class, 'parent_id');
    }

    /** @return HasOne<Proposal, $this> */
    public function revision(): HasOne
    {
        return $this->hasOne(Proposal::class, 'parent_id');
    }

    /** @return HasMany<Vote, $this> */
    public function votes(): HasMany
    {
        return $this->hasMany(Vote::class, 'proposal_id');
    }

    /** @return HasMany<RiskCheck, $this> */
    public function riskChecks(): HasMany
    {
        return $this->hasMany(RiskCheck::class, 'proposal_id');
    }

    /**
     * The newest risk check. Ordered rather than ofMany(): Postgres has no max(uuid), and an eager
     * load of a hasOne keeps the first row per parent, so the newest wins.
     *
     * @return HasOne<RiskCheck, $this>
     */
    public function latestRiskCheck(): HasOne
    {
        return $this->hasOne(RiskCheck::class, 'proposal_id')->orderByDesc('created_at')->orderByDesc('id');
    }

    /** @return HasMany<Order, $this> */
    public function orders(): HasMany
    {
        return $this->hasMany(Order::class, 'proposal_id');
    }

    /** True while the owner may still approve or edit it (status and window; not the settings version). */
    public function approvable(?Carbon $now = null): bool
    {
        return $this->status === 'awaiting_owner'
            && $this->approval_expires_at !== null
            && $this->approval_expires_at->greaterThan($now ?? Carbon::now());
    }

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'version' => 'integer',
            'entry_zone' => 'array',
            'take_profits' => 'array',
            'evidence' => 'array',
            'time_stop_minutes' => 'integer',
            'leverage_suggestion' => 'integer',
            'approval_expires_at' => 'datetime',
            'decided_at' => 'datetime',
            'approval_reminded_at' => 'datetime',
        ];
    }
}
