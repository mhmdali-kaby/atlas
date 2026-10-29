<?php

declare(strict_types=1);

namespace App\Support\Events;

use App\Http\Resources\WarroomMessageResource;
use App\Models\WarroomMessage;
use App\Models\WarroomThread;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Broadcast;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Facades\Validator;
use Illuminate\Validation\Rule;

/**
 * What `atlas:bridge` does with one stream entry (brief §3.2): a `warroom.message` is persisted
 * to warroom_messages (at most once per envelope id) and, only when newly stored, broadcast on
 * `private-warroom` as `warroom.message` with the WarroomMessageResource JSON. Any other event on
 * atlas.warroom, atlas.account or atlas.system is broadcast as-is on `private-warroom`,
 * `private-account` or `private-system`, named by its envelope `type`, with the envelope as the
 * payload. Other streams have no browser channel yet and are only acknowledged.
 *
 * Throws MalformedEvent for entries that can never succeed (the bridge acks and logs them); any
 * other exception is transient (the bridge leaves the entry pending and retries it).
 */
final class BridgeEventHandler
{
    /** Stream → private channel name (without the `private-` prefix). */
    public const CHANNELS = [
        'atlas.warroom' => 'warroom',
        'atlas.account' => 'account',
        'atlas.system' => 'system',
    ];

    public const MESSAGE_EVENT = 'warroom.message';

    public const OUTCOME_PERSISTED = 'persisted';

    public const OUTCOME_DUPLICATE = 'duplicate';

    public const OUTCOME_BROADCAST = 'broadcast';

    public const OUTCOME_IGNORED = 'ignored';

    /**
     * @return self::OUTCOME_*
     */
    public function handle(string $stream, ?string $raw): string
    {
        $envelope = $this->envelope($raw);

        if ($stream === 'atlas.warroom' && $envelope['type'] === self::MESSAGE_EVENT) {
            $message = $this->persist($envelope);
            if ($message === null) {
                return self::OUTCOME_DUPLICATE;
            }
            $this->broadcast('warroom', self::MESSAGE_EVENT, (new WarroomMessageResource($message))->resolve());

            return self::OUTCOME_PERSISTED;
        }

        $channel = self::CHANNELS[$stream] ?? null;
        if ($channel === null) {
            return self::OUTCOME_IGNORED;
        }
        $this->broadcast($channel, $envelope['type'], $envelope);

        return self::OUTCOME_BROADCAST;
    }

    /**
     * @return array{id: string, type: string, ts: string, env: 'demo'|'live', source: string, correlation_id: string|null, payload: array<string, mixed>}
     */
    private function envelope(?string $raw): array
    {
        if ($raw === null) {
            throw new MalformedEvent('entry has no envelope field');
        }

        try {
            $decoded = json_decode($raw, true, 64, JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            throw new MalformedEvent('envelope is not valid JSON');
        }
        if (! is_array($decoded) || array_is_list($decoded)) {
            throw new MalformedEvent('envelope is not a JSON object');
        }

        $this->validate($decoded, [
            'id' => ['required', 'uuid'],
            'type' => ['required', 'string', 'max:64', 'regex:/^[a-z0-9_]+(\.[a-z0-9_]+)+$/'],
            'ts' => ['required', 'string', 'date'],
            'env' => ['required', Rule::in(['demo', 'live'])],
            'source' => ['required', 'string', 'max:128'],
            'correlation_id' => ['present', 'nullable', 'string', 'max:128'],
            'payload' => ['present', 'array'],
        ], 'envelope');

        /** @var array{id: string, type: string, ts: string, env: 'demo'|'live', source: string, correlation_id: string|null, payload: array<string, mixed>} $decoded */
        return $decoded;
    }

    /**
     * Store the message unless a row with this envelope id already exists.
     *
     * @param  array{id: string, type: string, ts: string, env: 'demo'|'live', source: string, correlation_id: string|null, payload: array<string, mixed>}  $envelope
     */
    private function persist(array $envelope): ?WarroomMessage
    {
        if (WarroomMessage::query()->where('event_id', $envelope['id'])->exists()) {
            return null;
        }

        $payload = $envelope['payload'];
        $this->validate($payload, [
            'thread' => ['required_without:thread_id', 'array'],
            'thread.kind' => ['required_with:thread', Rule::in(WarroomThread::KINDS)],
            'thread.key' => ['required_with:thread', 'string', 'max:64'],
            'thread_id' => ['required_without:thread', 'uuid'],
            'author_type' => ['required', Rule::in(WarroomMessage::AUTHOR_TYPES)],
            'author' => ['required', 'string', 'max:64'],
            'kind' => ['required', 'string', 'max:32', 'regex:/^[a-z][a-z0-9_]*$/'],
            'content_md' => ['present', 'string'],
            'card' => ['nullable', 'array'],
            'reply_to' => ['nullable', 'uuid'],
            'mentions' => ['nullable', 'array', 'list'],
            'mentions.*' => ['string', 'max:64'],
            'reasoning_md' => ['nullable', 'string'],
            'tool_calls' => ['nullable', 'array', 'list'],
            'tool_calls.*' => ['array'],
            'tokens_in' => ['nullable', 'integer', 'min:0', 'max:2147483647'],
            'tokens_out' => ['nullable', 'integer', 'min:0', 'max:2147483647'],
            'cost_usd' => ['nullable', 'string', 'regex:/^\d{1,12}(\.\d{1,6})?$/'],
        ], 'payload');

        $env = $envelope['env'];
        $thread = $this->thread($env, $payload);

        $replyTo = $payload['reply_to'] ?? null;
        if (is_string($replyTo) && ! WarroomMessage::query()->whereKey($replyTo)->where('env', $env)->exists()) {
            throw new MalformedEvent('payload.reply_to is not a message in this env');
        }

        try {
            $message = DB::transaction(function () use ($envelope, $payload, $env, $thread, $replyTo): WarroomMessage {
                $message = new WarroomMessage([
                    'env' => $env,
                    'thread_id' => $thread->id,
                    'event_id' => $envelope['id'],
                    'author_type' => $payload['author_type'],
                    'author' => $payload['author'],
                    'kind' => $payload['kind'],
                    'content_md' => $payload['content_md'],
                    'card' => $payload['card'] ?? null,
                    'reply_to' => $replyTo,
                    'mentions' => $payload['mentions'] ?? [],
                    'reasoning_md' => $payload['reasoning_md'] ?? null,
                    'tool_calls' => $payload['tool_calls'] ?? null,
                    'tokens_in' => isset($payload['tokens_in']) ? (int) $payload['tokens_in'] : null,
                    'tokens_out' => isset($payload['tokens_out']) ? (int) $payload['tokens_out'] : null,
                    'cost_usd' => $payload['cost_usd'] ?? null,
                ]);
                // The message's time is when it was published, so a drained backlog keeps its order.
                $message->created_at = Carbon::parse($envelope['ts'])->utc();
                $message->save();

                return $message;
            });
        } catch (UniqueConstraintViolationException $e) {
            if (WarroomMessage::query()->where('event_id', $envelope['id'])->exists()) {
                return null;
            }
            throw $e;
        }

        // Read it back so card keys come out in jsonb order, as the API returns them.
        return $message->refresh();
    }

    /**
     * @param  array<string, mixed>  $payload
     */
    private function thread(string $env, array $payload): WarroomThread
    {
        $threadId = $payload['thread_id'] ?? null;
        if (is_string($threadId) && ! isset($payload['thread'])) {
            $thread = WarroomThread::query()->whereKey($threadId)->where('env', $env)->first();

            return $thread ?? throw new MalformedEvent('payload.thread_id is not a thread in this env');
        }

        /** @var array{kind: 'main'|'instrument'|'agent', key: string} $ref */
        $ref = $payload['thread'];
        if (($ref['kind'] === 'main') !== ($ref['key'] === WarroomThread::MAIN_KEY)) {
            throw new MalformedEvent('payload.thread key "main" is only for the main thread');
        }

        return WarroomThread::query()->firstOrCreate(
            ['env' => $env, 'kind' => $ref['kind'], 'key' => $ref['key']],
            ['title' => $ref['kind'] === 'main' ? 'Main' : $ref['key']],
        );
    }

    /**
     * @param  array<string, mixed>  $payload
     */
    private function broadcast(string $channel, string $event, array $payload): void
    {
        try {
            Broadcast::private($channel)->as($event)->with($payload)->sendNow();
        } catch (\Throwable $e) {
            // Postgres is the record and the SPA refetches on reconnect, so a Reverb outage must not
            // stall the stream. Log the failure class only: the message can quote the payload.
            Log::warning('atlas.bridge.broadcast_failed', [
                'channel' => 'private-'.$channel,
                'event' => $event,
                'exception' => $e::class,
            ]);
        }
    }

    /**
     * @param  array<array-key, mixed>  $data
     * @param  array<string, mixed>  $rules
     */
    private function validate(array $data, array $rules, string $prefix): void
    {
        $validator = Validator::make($data, $rules);
        if ($validator->fails()) {
            $fields = implode(', ', array_map(fn ($f) => $prefix.'.'.$f, array_keys($validator->failed())));
            throw new MalformedEvent("invalid fields: {$fields}");
        }
    }
}
