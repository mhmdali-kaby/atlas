<?php

declare(strict_types=1);

namespace App\Support\Proposals;

use App\Models\Proposal;
use App\Models\User;
use App\Support\Auditable;
use App\Support\Events\EventBus;
use App\Support\TradingCore;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use RuntimeException;

/**
 * The one approve / reject path for a proposal awaiting the owner (ATL-162, ATL-215, decision #254).
 * The web API (POST /api/proposals/{id}/approve|reject) and the Telegram buttons
 * (atlas:telegram-poll) both call it; only `actor` differs ("owner" or "telegram").
 *
 * - approve: only an awaiting_owner proposal inside its window whose newest risk check ran on the
 *   current settings version, then one signed call to trading_core
 *   POST /internal/proposals/{id}/execute with {"approved_by": "owner"} (a Telegram press is the
 *   owner's approval), which re-runs the Risk Engine before the Executor. Laravel never sends an
 *   order itself.
 * - reject: awaiting_owner / advise_only only, with a reason.
 *
 * Both write one audit_log row (after.actor = the actor) and publish on atlas.proposals.
 */
final class ProposalApprovalService
{
    public const ACTORS = ['owner', 'telegram'];

    /** The envelope source per actor (brief §3.2). */
    private const SOURCES = ['owner' => 'human:owner', 'telegram' => 'human:telegram'];

    public function __construct(private readonly Auditable $audit, private readonly EventBus $bus) {}

    public function approve(Proposal $proposal, string $actor, ?User $user = null): ApprovalOutcome
    {
        $actor = self::actor($actor);
        $check = $proposal->latestRiskCheck()->first();
        $blocked = ProposalView::approveBlocked($proposal, $check, ProposalView::settingsVersion($proposal->env));
        if ($blocked !== null) {
            return new ApprovalOutcome(false, $blocked, 409, $proposal);
        }

        $status = 200;
        $error = null;
        $body = [];
        try {
            $response = TradingCore::fromConfig()->post("/internal/proposals/{$proposal->id}/execute", ['approved_by' => 'owner']);
            $json = $response->json();
            $body = is_array($json) ? $json : [];
            if ($response->status() === 409 || $response->status() === 404) {
                [$status, $error] = [409, 'execution_refused'];
            } elseif (! $response->successful()) {
                [$status, $error] = [502, $body === [] ? 'trading_core_error' : 'execution_failed'];
            }
        } catch (ConnectionException|RuntimeException) {
            [$status, $error] = [502, 'trading_core_unavailable'];
        }

        $proposal->refresh();
        $outcome = [
            'ok' => $error === null,
            'error' => $error,
            'status' => $body['status'] ?? null,
            'reason' => $body['reason'] ?? null,
            'order_id' => $body['order_id'] ?? null,
            'cl_ord_id' => $body['cl_ord_id'] ?? null,
            'state' => $body['state'] ?? null,
            'risk_check_id' => is_array($body['risk_check'] ?? null) ? ($body['risk_check']['risk_check_id'] ?? null) : null,
        ];
        $this->audit->record('proposal.approved', 'proposals', $proposal->id, [
            'status' => 'awaiting_owner',
            'risk_check_id' => $check?->id,
        ], [...$outcome, 'proposal_status' => $proposal->status, 'actor' => $actor], $user);
        $this->publish('proposal.approved', $proposal, $actor, [...$outcome, 'decided_by' => 'owner', 'via' => $actor]);

        return new ApprovalOutcome($error === null, $error, $status, $proposal, $body);
    }

    public function reject(Proposal $proposal, string $actor, string $reason, ?User $user = null): ApprovalOutcome
    {
        $actor = self::actor($actor);

        return DB::transaction(function () use ($proposal, $actor, $reason, $user): ApprovalOutcome {
            /** @var Proposal $locked */
            $locked = Proposal::query()->lockForUpdate()->findOrFail($proposal->id);
            if (! in_array($locked->status, ProposalRules::REJECTABLE, true)) {
                return new ApprovalOutcome(false, 'not_rejectable', 409, $locked);
            }
            $before = ['status' => $locked->status, 'approval_expires_at' => $locked->approval_expires_at?->toIso8601ZuluString()];
            $locked->fill([
                'status' => 'rejected',
                'status_reason' => 'owner_rejected',
                'owner_note' => $reason,
                'decided_by' => 'owner',
                'decided_at' => Carbon::now(),
                'approval_expires_at' => null,
            ])->save();
            $this->audit->record('proposal.rejected', 'proposals', $locked->id, $before, [
                'status' => 'rejected',
                'status_reason' => 'owner_rejected',
                'reason' => $reason,
                'actor' => $actor,
            ], $user);
            $this->publish('proposal.rejected', $locked, $actor, ['decided_by' => 'owner', 'via' => $actor, 'reason' => $reason]);

            return new ApprovalOutcome(true, null, 200, $locked);
        });
    }

    private static function actor(string $actor): string
    {
        if (! in_array($actor, self::ACTORS, true)) {
            throw new \InvalidArgumentException("Unknown approval actor: {$actor}");
        }

        return $actor;
    }

    /**
     * @param  array<string, mixed>  $extra
     */
    private function publish(string $type, Proposal $proposal, string $actor, array $extra): void
    {
        $this->bus->publish(
            stream: 'atlas.proposals',
            type: $type,
            env: $proposal->env,
            source: self::SOURCES[$actor],
            payload: [
                'proposal_id' => $proposal->id,
                'version' => $proposal->version,
                'cycle_id' => $proposal->cycle_id,
                'inst_id' => $proposal->inst_id,
                'side' => $proposal->side,
                'status' => $proposal->status,
                'status_reason' => $proposal->status_reason,
                'approval_expires_at' => $proposal->approval_expires_at?->utc()->toIso8601ZuluString(),
                ...$extra,
            ],
            correlationId: $proposal->cycle_id !== null ? 'cycle-'.$proposal->cycle_id : $proposal->id,
        );
    }
}
