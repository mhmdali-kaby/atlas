<?php

declare(strict_types=1);

namespace App\Http\Controllers\Warroom;

use App\Http\Controllers\Controller;
use App\Http\Requests\Warroom\ListMessagesRequest;
use App\Http\Requests\Warroom\ReplayRequest;
use App\Http\Requests\Warroom\StoreMessageRequest;
use App\Http\Requests\Warroom\ThreadsRequest;
use App\Http\Resources\WarroomMessageResource;
use App\Http\Resources\WarroomThreadResource;
use App\Models\User;
use App\Models\WarroomMessage;
use App\Models\WarroomThread;
use App\Support\Auditable;
use App\Support\Events\EventBus;
use App\Support\Warroom\MentionParser;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Resources\Json\AnonymousResourceCollection;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

/**
 * War Room API (brief §12, §16). Reads Postgres; owner messages are persisted here and then
 * published on atlas.warroom with envelope id = the row's event_id, so the bridge dedupes them.
 */
class WarroomController extends Controller
{
    /**
     * The nine agents (slug, display name, one-word @handle, aliases) for the composer's
     * mention autocomplete and the sidebar. Static config; not env-specific.
     */
    public function agents(): JsonResponse
    {
        return response()->json(['data' => MentionParser::directory()]);
    }

    public function threads(ThreadsRequest $request): AnonymousResourceCollection
    {
        $lastMessageAt = WarroomMessage::query()
            ->selectRaw('max(created_at)')
            ->whereColumn('warroom_messages.thread_id', 'warroom_threads.id');

        $threads = WarroomThread::query()
            ->select('warroom_threads.*')
            ->selectSub($lastMessageAt, 'last_message_at')
            ->where('env', $request->env())
            ->orderByRaw("case when kind = 'main' then 0 else 1 end")
            ->orderByRaw('last_message_at desc nulls last')
            ->orderBy('title')
            ->get();

        return WarroomThreadResource::collection($threads);
    }

    public function messages(ListMessagesRequest $request): AnonymousResourceCollection
    {
        $page = WarroomMessage::query()
            ->where('env', $request->env())
            ->where('thread_id', $request->threadId())
            ->orderByDesc('created_at')
            ->orderByDesc('id')
            ->cursorPaginate($request->limit())
            ->withQueryString();

        return WarroomMessageResource::collection($page);
    }

    public function store(StoreMessageRequest $request, Auditable $audit, EventBus $bus, MentionParser $mentions): JsonResponse
    {
        /** @var User $user */
        $user = $request->user();
        $env = $request->env();

        $message = DB::transaction(function () use ($request, $audit, $bus, $mentions, $user, $env): WarroomMessage {
            /** @var WarroomThread $thread */
            $thread = WarroomThread::query()->findOrFail($request->threadId());

            $message = WarroomMessage::query()->create([
                'env' => $env,
                'thread_id' => $thread->id,
                'event_id' => (string) Str::uuid(),
                'author_type' => 'human',
                'author' => Str::limit($user->name, 64, ''),
                'kind' => 'text',
                'content_md' => $request->contentMd(),
                'reply_to' => $request->replyTo(),
                'mentions' => $mentions->parse($request->contentMd()),
            ]);
            $message->refresh();

            $audit->record('warroom.message_posted', 'warroom_messages', $message->id, null, [
                'env' => $env,
                'thread_id' => $thread->id,
                'reply_to' => $message->reply_to,
                'mentions' => $message->mentions,
                'content_chars' => mb_strlen($message->content_md),
            ], $user);

            // Inside the transaction: if Redis is down the post fails as a whole. A consumer that
            // sees the event before the commit blocks on the unique event_id, then dedupes.
            $bus->publish(
                stream: 'atlas.warroom',
                type: 'warroom.message',
                env: $env,
                source: 'human:owner',
                payload: [
                    'thread' => ['kind' => $thread->kind, 'key' => $thread->key],
                    'thread_id' => $thread->id,
                    'message_id' => $message->id,
                    'author_type' => 'human',
                    'author' => $message->author,
                    'kind' => 'text',
                    'content_md' => $message->content_md,
                    'reply_to' => $message->reply_to,
                    'mentions' => $message->mentions,
                    'created_at' => $message->created_at->utc()->toIso8601ZuluString('microsecond'),
                ],
                id: $message->event_id,
            );

            return $message;
        });

        return (new WarroomMessageResource($message))->response()->setStatusCode(201);
    }

    public function replay(ReplayRequest $request): AnonymousResourceCollection
    {
        [$start, $end] = $request->dayWindowUtc();

        $messages = WarroomMessage::query()
            ->where('env', $request->env())
            ->where('created_at', '>=', $start->format('Y-m-d H:i:s.uP'))
            ->where('created_at', '<', $end->format('Y-m-d H:i:s.uP'))
            ->orderBy('created_at')
            ->orderBy('id')
            ->get();

        return WarroomMessageResource::collection($messages)->additional([
            'meta' => [
                'date' => $request->validated('date'),
                'timezone' => config('atlas_defaults.timezone.display'),
                'from' => $start->toIso8601ZuluString('microsecond'),
                'to' => $end->toIso8601ZuluString('microsecond'),
            ],
        ]);
    }
}
