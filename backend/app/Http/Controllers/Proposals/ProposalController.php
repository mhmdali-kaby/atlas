<?php

declare(strict_types=1);

namespace App\Http\Controllers\Proposals;

use App\Http\Controllers\Controller;
use App\Http\Requests\Proposals\AskTeamRequest;
use App\Http\Requests\Proposals\EditProposalRequest;
use App\Http\Requests\Proposals\ListProposalsRequest;
use App\Http\Requests\Proposals\RejectProposalRequest;
use App\Http\Resources\ProposalResource;
use App\Models\Proposal;
use App\Models\User;
use App\Models\WarroomMessage;
use App\Models\WarroomThread;
use App\Support\Auditable;
use App\Support\Events\EventBus;
use App\Support\Proposals\Dec;
use App\Support\Proposals\ProposalRules;
use App\Support\Proposals\ProposalView;
use App\Support\TradingCore;
use App\Support\Warroom\MentionParser;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Resources\Json\AnonymousResourceCollection;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Gate;
use Illuminate\Support\Str;
use RuntimeException;

/**
 * /api/proposals (ATL-162, brief §12 proposal actions, §16).
 *
 * - approve: only an awaiting_owner proposal inside its window whose newest risk check ran on the
 *   current settings version. Laravel then makes one signed call to trading_core
 *   POST /internal/proposals/{id}/execute, which re-runs the Risk Engine and only then calls the
 *   Executor. Laravel never sends an order itself.
 * - reject {reason}, edit {size, stop, targets} (stop only tightens; creates version + 1 and
 *   re-runs the risk check through POST /internal/risk/evaluate/{id}), ask-team {question}.
 *
 * Every write is audited and published on atlas.proposals (brief §3.2 envelope).
 */
class ProposalController extends Controller
{
    private const SOURCE = 'human:owner';

    /** Query-builder updates bypass the model's dateFormat; keep the microseconds. */
    private const TS = 'Y-m-d H:i:s.uP';

    public function __construct(private readonly Auditable $audit, private readonly EventBus $bus) {}

    public function index(ListProposalsRequest $request): AnonymousResourceCollection
    {
        Gate::authorize('viewAny', Proposal::class);
        $query = Proposal::query()->where('env', $request->env())->with('latestRiskCheck');
        if ($request->statuses() !== []) {
            $query->whereIn('status', $request->statuses());
        }
        if ($request->instId() !== null) {
            $query->where('inst_id', $request->instId());
        }
        if (($bounds = $request->dayBounds()) !== null) {
            $query->where('created_at', '>=', $bounds[0])->where('created_at', '<', $bounds[1]);
        }
        $request->attributes->set(ProposalResource::SETTINGS_VERSION, ProposalView::settingsVersion($request->env()));
        $page = $query->orderByDesc('created_at')->orderByDesc('id')
            ->cursorPaginate($request->limit())
            ->withQueryString();

        return ProposalResource::collection($page);
    }

    public function show(Proposal $proposal): ProposalResource
    {
        Gate::authorize('view', $proposal);

        return (new ProposalResource($proposal))->detailed();
    }

    public function approve(Proposal $proposal): JsonResponse
    {
        Gate::authorize('act', $proposal);
        $check = $proposal->latestRiskCheck()->first();
        $blocked = ProposalView::approveBlocked($proposal, $check, ProposalView::settingsVersion($proposal->env));
        if ($blocked !== null) {
            return $this->conflict($blocked, $proposal);
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
        ], [...$outcome, 'proposal_status' => $proposal->status]);
        $this->publish('proposal.approved', $proposal, [...$outcome, 'decided_by' => 'owner']);

        return response()->json([
            'ok' => $error === null,
            'error' => $error,
            'message' => $error === null ? null : __("proposals.{$error}"),
            'execution' => $body,
            'proposal' => ProposalView::summary($proposal),
        ], $status);
    }

    public function reject(RejectProposalRequest $request, Proposal $proposal): JsonResponse
    {
        Gate::authorize('act', $proposal);

        $result = DB::transaction(function () use ($request, $proposal): Proposal|JsonResponse {
            /** @var Proposal $locked */
            $locked = Proposal::query()->lockForUpdate()->findOrFail($proposal->id);
            if (! in_array($locked->status, ProposalRules::REJECTABLE, true)) {
                return $this->conflict('not_rejectable', $locked);
            }
            $before = ['status' => $locked->status, 'approval_expires_at' => $locked->approval_expires_at?->toIso8601ZuluString()];
            $locked->fill([
                'status' => 'rejected',
                'status_reason' => 'owner_rejected',
                'owner_note' => $request->reason(),
                'decided_by' => 'owner',
                'decided_at' => Carbon::now(),
                'approval_expires_at' => null,
            ])->save();
            $this->audit->record('proposal.rejected', 'proposals', $locked->id, $before, [
                'status' => 'rejected',
                'status_reason' => 'owner_rejected',
                'reason' => $request->reason(),
            ]);
            $this->publish('proposal.rejected', $locked, ['decided_by' => 'owner', 'reason' => $request->reason()]);

            return $locked;
        });

        if ($result instanceof JsonResponse) {
            return $result;
        }

        return response()->json(['ok' => true, 'proposal' => ProposalView::summary($result)]);
    }

    public function update(EditProposalRequest $request, Proposal $proposal): JsonResponse
    {
        Gate::authorize('act', $proposal);

        $created = DB::transaction(function () use ($request, $proposal): array|JsonResponse {
            /** @var Proposal $parent */
            $parent = Proposal::query()->lockForUpdate()->findOrFail($proposal->id);
            if ($parent->kind !== 'entry') {
                return $this->conflict('not_editable', $parent);
            }
            if ($parent->status !== 'awaiting_owner') {
                return $this->conflict('not_awaiting_owner', $parent);
            }
            if (! $parent->approvable()) {
                return $this->conflict('approval_expired', $parent);
            }

            $before = self::editable($parent);
            $revision = $parent->replicate(['approval_expires_at', 'status_reason', 'owner_note', 'decided_by', 'decided_at', 'approval_reminded_at']);
            $revision->fill([
                'version' => $parent->version + 1,
                'parent_id' => $parent->id,
                'author' => 'owner',
                // passed = evaluable by the Risk Engine (risk_rules.evaluable_statuses).
                'status' => 'passed',
                'approval_expires_at' => null,
            ]);
            if (($size = $request->size()) !== null) {
                $revision->size_suggestion_pct = (string) $size;
            }
            if (($stop = $request->stop()) !== null) {
                $revision->stop = (string) $stop;
            }
            if (($targets = $request->targets()) !== null) {
                $revision->take_profits = array_map(
                    static fn (array $tp): array => ['price' => (string) $tp['price'], 'pct' => (string) $tp['pct']],
                    $targets,
                );
            }
            $revision->save();

            $expires = $parent->approval_expires_at;
            $parent->fill([
                'status' => 'cancelled',
                'status_reason' => 'revised',
                'decided_by' => 'owner',
                'decided_at' => Carbon::now(),
                'approval_expires_at' => null,
            ])->save();

            return [$parent, $revision, $before, $expires];
        });
        if ($created instanceof JsonResponse) {
            return $created;
        }
        /** @var Proposal $parent */
        /** @var Proposal $revision */
        [$parent, $revision, $before, $expires] = $created;

        $error = null;
        $body = [];
        try {
            $response = TradingCore::fromConfig()->post("/internal/risk/evaluate/{$revision->id}");
            $json = $response->json();
            $body = is_array($json) ? $json : [];
            if (! $response->successful()) {
                $error = $response->status() === 409 ? 'risk_engine_unavailable' : 'trading_core_error';
            }
        } catch (ConnectionException|RuntimeException) {
            $error = 'trading_core_unavailable';
        }

        if ($error !== null) {
            // No risk check, no edit: put the original back as it was.
            DB::transaction(function () use ($parent, $revision, $expires): void {
                Proposal::query()->whereKey($revision->id)->delete();
                Proposal::query()->whereKey($parent->id)->update([
                    'status' => 'awaiting_owner',
                    'status_reason' => null,
                    'decided_by' => null,
                    'decided_at' => null,
                    'approval_expires_at' => $expires?->format(self::TS),
                ]);
            });

            return response()->json([
                'ok' => false,
                'error' => $error,
                'message' => __("proposals.{$error}"),
                'detail' => $body['detail'] ?? null,
            ], $error === 'risk_engine_unavailable' ? 409 : 502);
        }

        $result = $body['result'] ?? null;
        [$status, $reason, $until] = match (true) {
            $result === 'rejected' => ['rejected', 'risk_rejected', null],
            ($body['decision'] ?? null) === 'advise_only' => ['advise_only', null, null],
            default => ['awaiting_owner', null, $expires],
        };
        Proposal::query()->whereKey($revision->id)->where('status', 'risk_checked')->update([
            'status' => $status,
            'status_reason' => $reason,
            'approval_expires_at' => $until?->format(self::TS),
            'updated_at' => Carbon::now()->format(self::TS),
        ]);
        $revision->refresh();

        $this->audit->record('proposal.edited', 'proposals', $parent->id, $before, [
            ...self::editable($revision),
            'revision_id' => $revision->id,
            'version' => $revision->version,
            'risk_check_id' => $body['risk_check_id'] ?? null,
            'result' => $result,
            'status' => $revision->status,
        ]);
        $this->publish('proposal.revised', $revision, [
            'parent_id' => $parent->id,
            'author' => 'owner',
            'changes' => array_keys(array_filter([
                'size' => $request->size() !== null,
                'stop' => $request->stop() !== null,
                'targets' => $request->targets() !== null,
            ])),
            'risk_check_id' => $body['risk_check_id'] ?? null,
            'result' => $result,
            'decision' => $body['decision'] ?? null,
        ]);

        return response()->json([
            'ok' => true,
            'proposal' => ProposalView::detail($revision),
            'risk_check' => $body,
        ]);
    }

    public function askTeam(AskTeamRequest $request, Proposal $proposal, MentionParser $mentions): JsonResponse
    {
        Gate::authorize('act', $proposal);
        /** @var User $user */
        $user = $request->user();
        $handle = collect(MentionParser::directory())->firstWhere('slug', 'orchestrator')['handle'] ?? 'Orchestrator';
        $side = $proposal->side !== null ? $proposal->side.' ' : '';
        $content = __('proposals.ask_team_message', [
            'handle' => $handle,
            'question' => $request->question(),
            'side' => $side,
            'inst' => $proposal->inst_id,
            'version' => $proposal->version,
        ]);

        $message = DB::transaction(function () use ($proposal, $user, $content, $mentions, $request): WarroomMessage {
            $thread = WarroomThread::query()->firstOrCreate(
                ['env' => $proposal->env, 'kind' => 'main', 'key' => WarroomThread::MAIN_KEY],
                ['title' => 'Main'],
            );
            $found = $mentions->parse($content);
            if (! in_array('orchestrator', $found, true)) {
                $found[] = 'orchestrator';
            }
            $card = ['type' => 'proposal_question', 'proposal_id' => $proposal->id, 'version' => $proposal->version];
            $message = WarroomMessage::query()->create([
                'env' => $proposal->env,
                'thread_id' => $thread->id,
                'event_id' => (string) Str::uuid(),
                'author_type' => 'human',
                'author' => Str::limit($user->name, 64, ''),
                'kind' => 'text',
                'content_md' => $content,
                'card' => $card,
                'mentions' => $found,
            ]);
            $message->refresh();

            $this->audit->record('proposal.asked_team', 'proposals', $proposal->id, null, [
                'message_id' => $message->id,
                'question_chars' => mb_strlen($request->question()),
            ], $user);
            $this->bus->publish(
                stream: 'atlas.warroom',
                type: 'warroom.message',
                env: $proposal->env,
                source: self::SOURCE,
                payload: [
                    'thread' => ['kind' => $thread->kind, 'key' => $thread->key],
                    'thread_id' => $thread->id,
                    'message_id' => $message->id,
                    'author_type' => 'human',
                    'author' => $message->author,
                    'kind' => 'text',
                    'content_md' => $message->content_md,
                    'card' => $card,
                    'reply_to' => null,
                    'mentions' => $message->mentions,
                    'created_at' => $message->created_at->utc()->toIso8601ZuluString('microsecond'),
                ],
                correlationId: self::correlationId($proposal),
                id: $message->event_id,
            );
            $this->publish('proposal.asked_team', $proposal, ['message_id' => $message->id]);

            return $message;
        });

        return response()->json(['ok' => true, 'message_id' => $message->id, 'thread_id' => $message->thread_id], 201);
    }

    private function conflict(string $error, Proposal $proposal): JsonResponse
    {
        return response()->json([
            'ok' => false,
            'error' => $error,
            'message' => __("proposals.{$error}"),
            'proposal' => ProposalView::summary($proposal),
        ], 409);
    }

    /**
     * @param  array<string, mixed>  $extra
     */
    private function publish(string $type, Proposal $proposal, array $extra): void
    {
        $this->bus->publish(
            stream: 'atlas.proposals',
            type: $type,
            env: $proposal->env,
            source: self::SOURCE,
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
            correlationId: self::correlationId($proposal),
        );
    }

    private static function correlationId(Proposal $proposal): string
    {
        return $proposal->cycle_id !== null ? 'cycle-'.$proposal->cycle_id : $proposal->id;
    }

    /**
     * @return array{size: string|null, stop: string|null, targets: list<array<string, mixed>>}
     */
    private static function editable(Proposal $p): array
    {
        return [
            'size' => Dec::out($p->size_suggestion_pct),
            'stop' => Dec::out($p->stop),
            'targets' => $p->take_profits,
        ];
    }
}
