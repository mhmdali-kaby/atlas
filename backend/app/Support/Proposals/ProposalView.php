<?php

declare(strict_types=1);

namespace App\Support\Proposals;

use App\Models\Order;
use App\Models\Proposal;
use App\Models\RiskCheck;
use App\Models\Setting;
use App\Models\Vote;
use App\Models\WarroomMessage;
use Illuminate\Support\Carbon;
use Illuminate\Support\Collection;
use Illuminate\Support\Facades\DB;

/**
 * The JSON shape of a proposal for /api/proposals (ATL-162). `summary()` is a list row;
 * `detail()` adds the version chain, the votes on every version, the newest Devil's Advocate
 * review, every risk check and the orders.
 */
final class ProposalView
{
    /**
     * @return array<string, mixed>
     */
    public static function summary(Proposal $p, ?int $settingsVersion = null, ?Carbon $now = null): array
    {
        $check = $p->relationLoaded('latestRiskCheck') ? $p->latestRiskCheck : $p->latestRiskCheck()->first();
        $settingsVersion ??= self::settingsVersion($p->env);
        $blocked = self::approveBlocked($p, $check, $settingsVersion, $now);

        return [
            'id' => $p->id,
            'env' => $p->env,
            'cycle_id' => $p->cycle_id,
            'version' => $p->version,
            'parent_id' => $p->parent_id,
            'kind' => $p->kind,
            'author' => $p->author,
            'inst_id' => $p->inst_id,
            'side' => $p->side,
            'order_type' => $p->order_type,
            'entry' => Dec::out($p->entry),
            'entry_zone' => is_array($p->entry_zone) ? array_map(Dec::out(...), $p->entry_zone) : null,
            'stop' => Dec::out($p->stop),
            'take_profits' => array_map(
                static fn (array $tp): array => ['price' => Dec::out($tp['price'] ?? null), 'pct' => Dec::out($tp['pct'] ?? null)],
                $p->take_profits,
            ),
            'time_stop_minutes' => $p->time_stop_minutes,
            'size_suggestion_pct' => Dec::out($p->size_suggestion_pct),
            'close_pct' => Dec::out($p->close_pct),
            'leverage_suggestion' => $p->leverage_suggestion,
            'expected_r' => Dec::out($p->expected_r),
            'probability' => Dec::out($p->probability),
            'rationale_md' => $p->rationale_md,
            'invalidation' => $p->invalidation,
            'evidence' => $p->evidence,
            'status' => $p->status,
            'status_reason' => $p->status_reason,
            'owner_note' => $p->owner_note,
            'decided_by' => $p->decided_by,
            'decided_at' => self::iso($p->decided_at),
            'approval_expires_at' => self::iso($p->approval_expires_at),
            'approval_reminded_at' => self::iso($p->approval_reminded_at),
            'created_at' => self::iso($p->created_at),
            'updated_at' => self::iso($p->updated_at),
            'risk_check' => $check === null ? null : self::riskCheck($check),
            'actions' => [
                'approve' => $blocked === null,
                'approve_blocked_reason' => $blocked,
                'edit' => $p->kind === 'entry' && $p->approvable($now),
                'reject' => in_array($p->status, ProposalRules::REJECTABLE, true),
                'ask_team' => true,
            ],
        ];
    }

    /**
     * @return array<string, mixed>
     */
    public static function detail(Proposal $p): array
    {
        $chain = self::chain($p);
        $ids = $chain->pluck('id')->all();

        $votes = Vote::query()->whereIn('proposal_id', $ids)->orderBy('created_at')->orderBy('id')->get();
        $checks = RiskCheck::query()->whereIn('proposal_id', $ids)->orderBy('created_at')->orderBy('id')->get();
        $orders = Order::query()->whereIn('proposal_id', $ids)->orderBy('created_at')->orderBy('id')->get();
        $review = WarroomMessage::query()
            ->where('env', $p->env)
            ->where('author', 'devils_advocate')
            ->whereRaw("card->>'type' = 'da_review'")
            ->whereIn(DB::raw("card->>'proposal_id'"), $ids)
            ->orderByDesc('created_at')
            ->orderByDesc('id')
            ->first();

        return [
            ...self::summary($p),
            'versions' => $chain->map(static fn (Proposal $v): array => [
                'id' => $v->id,
                'version' => $v->version,
                'author' => $v->author,
                'status' => $v->status,
                'status_reason' => $v->status_reason,
                'stop' => Dec::out($v->stop),
                'size_suggestion_pct' => Dec::out($v->size_suggestion_pct),
                'take_profits' => array_map(
                    static fn (array $tp): array => ['price' => Dec::out($tp['price'] ?? null), 'pct' => Dec::out($tp['pct'] ?? null)],
                    $v->take_profits,
                ),
                'created_at' => self::iso($v->created_at),
            ])->values()->all(),
            'votes' => $votes->map(static fn (Vote $v): array => [
                'id' => $v->id,
                'proposal_id' => $v->proposal_id,
                'agent' => $v->agent,
                'vote' => $v->vote,
                'confidence' => Dec::out($v->confidence),
                'reason' => $v->reason,
                'fix' => $v->fix,
                'created_at' => self::iso($v->created_at),
            ])->values()->all(),
            'da_review' => $review === null ? null : [
                'message_id' => $review->id,
                'proposal_id' => $review->card['proposal_id'] ?? null,
                'risk_rating' => $review->card['risk_rating'] ?? null,
                'content_md' => $review->content_md,
                'card' => $review->card,
                'created_at' => self::iso($review->created_at),
            ],
            'risk_checks' => $checks->map(self::riskCheck(...))->values()->all(),
            'orders' => $orders->map(static fn (Order $o): array => [
                'id' => $o->id,
                'proposal_id' => $o->proposal_id,
                'risk_check_id' => $o->risk_check_id,
                'cl_ord_id' => $o->cl_ord_id,
                'ord_id' => $o->ord_id,
                'inst_id' => $o->inst_id,
                'side' => $o->side,
                'ord_type' => $o->ord_type,
                'purpose' => $o->purpose,
                'px' => Dec::out($o->px),
                'sz' => Dec::out($o->sz),
                'filled_sz' => Dec::out($o->filled_sz),
                'avg_px' => Dec::out($o->avg_px),
                'lever' => $o->lever,
                'state' => $o->state,
                'reduce_only' => $o->reduce_only,
                'last_error_code' => $o->last_error_code,
                'last_error_msg' => $o->last_error_msg,
                'created_at' => self::iso($o->created_at),
                'updated_at' => self::iso($o->updated_at),
            ])->values()->all(),
        ];
    }

    /**
     * @return array<string, mixed>
     */
    public static function riskCheck(RiskCheck $c): array
    {
        return [
            'id' => $c->id,
            'proposal_id' => $c->proposal_id,
            'settings_version' => $c->settings_version,
            'result' => $c->result,
            'decision' => $c->decision,
            'reason' => $c->reason,
            'final_contracts' => Dec::out($c->final_size),
            'final_notional_usd' => Dec::out($c->final_notional_usd),
            'leverage' => $c->leverage,
            'checks' => $c->checks,
            'created_at' => self::iso($c->created_at),
        ];
    }

    /** Why Approve is not possible now, or null when it is (the same rules the endpoint enforces). */
    public static function approveBlocked(Proposal $p, ?RiskCheck $check, ?int $settingsVersion, ?Carbon $now = null): ?string
    {
        if ($p->status !== 'awaiting_owner') {
            return 'not_awaiting_owner';
        }
        if (! $p->approvable($now)) {
            return 'approval_expired';
        }
        if ($check === null || ! in_array($check->result, ['approved', 'resized'], true)) {
            return 'risk_check_missing';
        }
        if ($settingsVersion === null || $check->settings_version !== $settingsVersion) {
            return 'settings_changed';
        }

        return null;
    }

    public static function settingsVersion(string $env): ?int
    {
        $version = Setting::query()->where('env', $env)->value('version');

        return is_numeric($version) ? (int) $version : null;
    }

    /**
     * Every version of the proposal, oldest first (parents, the proposal itself, then revisions).
     *
     * @return Collection<int, Proposal>
     */
    public static function chain(Proposal $p): Collection
    {
        $before = [];
        $cursor = $p;
        while ($cursor->parent_id !== null && count($before) < 20) {
            $parent = Proposal::query()->find($cursor->parent_id);
            if ($parent === null) {
                break;
            }
            array_unshift($before, $parent);
            $cursor = $parent;
        }
        $after = [];
        $cursor = $p;
        while (count($after) < 20) {
            $child = Proposal::query()->where('parent_id', $cursor->id)->first();
            if ($child === null) {
                break;
            }
            $after[] = $child;
            $cursor = $child;
        }

        return collect([...$before, $p, ...$after]);
    }

    private static function iso(?Carbon $at): ?string
    {
        return $at?->utc()->toIso8601ZuluString('microsecond');
    }
}
