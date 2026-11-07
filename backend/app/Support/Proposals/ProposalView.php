<?php

declare(strict_types=1);

namespace App\Support\Proposals;

use App\Models\Order;
use App\Models\Proposal;
use App\Models\RiskCheck;
use App\Models\Setting;
use App\Models\Vote;
use App\Models\WarroomMessage;
use Illuminate\Database\Eloquent\Builder;
use Illuminate\Support\Carbon;
use Illuminate\Support\Collection;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

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
            ...MarkPrice::of($p->env, $p->inst_id),
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
            'probability_basis' => self::probabilityBasis($p->probability_basis),
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
            'evidence' => self::evidence($p),
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

    /**
     * The proposal's evidence refs, each resolved to the War Room message that posted it (ATL-190),
     * in one query. A ref's id is matched as the message id itself (the Strategist cites the research
     * message it read), and by type: a news item or cluster → the `news_alert` that announced it, a
     * research report → a card naming it as `report_id`, a proposal → its `proposal` card. The oldest
     * match is the message that posted it. `label` names the poster for the chip; `message_id` is null
     * when no message in this env posted it.
     *
     * @return list<array{type: string, id: string, label: string|null, message_id: string|null}>
     */
    public static function evidence(Proposal $p): array
    {
        $refs = [];
        foreach ($p->evidence as $ref) {
            if (is_string($ref['type'] ?? null) && is_string($ref['id'] ?? null)) {
                $refs[] = ['type' => $ref['type'], 'id' => $ref['id']];
            }
        }
        if ($refs === []) {
            return [];
        }

        $ids = array_values(array_unique(array_column($refs, 'id')));
        $uuids = array_values(array_filter($ids, static fn (string $id): bool => Str::isUuid($id)));
        $news = self::idsOfType($refs, self::NEWS_REF_TYPES);
        $reports = self::idsOfType($refs, ['research_report']);
        $proposals = self::idsOfType($refs, ['proposal']);

        $messages = WarroomMessage::query()
            ->select(['id', 'author', 'kind', 'card', 'created_at'])
            ->where('env', $p->env)
            ->where(static function (Builder $q) use ($uuids, $news, $reports, $proposals): void {
                $q->whereRaw('false');
                if ($uuids !== []) {
                    $q->orWhereIn('id', $uuids);
                }
                if ($news !== []) {
                    $q->orWhere(static fn (Builder $w) => $w->where('kind', 'news_alert')->where(
                        static fn (Builder $x) => $x->whereIn(DB::raw("card->>'news_item_id'"), $news)->orWhereIn(DB::raw("card->>'cluster_id'"), $news),
                    ));
                }
                if ($reports !== []) {
                    $q->orWhereIn(DB::raw("card->>'report_id'"), $reports);
                }
                if ($proposals !== []) {
                    $q->orWhere(static fn (Builder $w) => $w->where('kind', 'proposal')->whereIn(DB::raw("card->>'proposal_id'"), $proposals));
                }
            })
            ->orderBy('created_at')
            ->orderBy('id')
            ->get();

        $out = [];
        foreach ($refs as $ref) {
            $found = $messages->first(static function (WarroomMessage $m) use ($ref): bool {
                if ($m->id === $ref['id']) {
                    return true;
                }
                $card = is_array($m->card) ? $m->card : [];
                $is = static fn (string $key): bool => ($card[$key] ?? null) === $ref['id'];

                return match (true) {
                    in_array($ref['type'], self::NEWS_REF_TYPES, true) => $m->kind === 'news_alert' && ($is('news_item_id') || $is('cluster_id')),
                    $ref['type'] === 'research_report' => $is('report_id'),
                    $ref['type'] === 'proposal' => $m->kind === 'proposal' && $is('proposal_id'),
                    default => false,
                };
            });
            $out[] = [
                'type' => $ref['type'],
                'id' => $ref['id'],
                'label' => $found === null ? null : self::evidenceLabel($found),
                'message_id' => $found?->id,
            ];
        }

        return $out;
    }

    /** Evidence ref types that point at a news item or a news cluster. */
    private const NEWS_REF_TYPES = ['news', 'news_item', 'news_cluster', 'news_alert'];

    /** Who posted the evidence, as the chip's label key (design v14 `pr.ev.*`). */
    private static function evidenceLabel(WarroomMessage $m): string
    {
        if ($m->kind === 'news_alert') {
            return 'news';
        }

        return match ($m->author) {
            'technical_analyst' => 'technical',
            'derivatives_sentiment' => 'positioning',
            'historian' => 'historian',
            'news_intelligence', 'system:news' => 'news',
            'market_scout' => 'scout',
            'strategist' => 'strategist',
            'devils_advocate' => 'devils_advocate',
            'portfolio_manager' => 'portfolio_manager',
            default => 'other',
        };
    }

    /**
     * @param  list<array{type: string, id: string}>  $refs
     * @param  list<string>  $types
     * @return list<string>
     */
    private static function idsOfType(array $refs, array $types): array
    {
        $ids = [];
        foreach ($refs as $ref) {
            if (in_array($ref['type'], $types, true)) {
                $ids[] = $ref['id'];
            }
        }

        return array_values(array_unique($ids));
    }

    /**
     * The Strategist's odds basis (ATL-189), or null when absent or malformed. `reason` is model
     * text: the client renders it as text.
     *
     * @param  array<string, mixed>|null  $basis
     * @return array{analog_hit_rate: string|null, n: int|null, adjustment_pts: string|null, reason: string}|null
     */
    private static function probabilityBasis(?array $basis): ?array
    {
        if ($basis === null) {
            return null;
        }
        $rate = $basis['analog_hit_rate'] ?? null;
        $adjust = $basis['adjustment_pts'] ?? null;
        $n = $basis['n'] ?? null;
        $reason = $basis['reason'] ?? null;
        if (! is_numeric($rate) || ! is_numeric($adjust) || ! is_string($reason)) {
            return null;
        }

        return [
            'analog_hit_rate' => Dec::out((string) $rate),
            'n' => is_int($n) ? $n : null,
            'adjustment_pts' => Dec::out((string) $adjust),
            'reason' => $reason,
        ];
    }

    private static function iso(?Carbon $at): ?string
    {
        return $at?->utc()->toIso8601ZuluString('microsecond');
    }
}
