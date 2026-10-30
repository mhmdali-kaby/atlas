<?php

declare(strict_types=1);

namespace App\Support\Events;

use App\Http\Resources\WarroomMessageResource;
use App\Models\WarroomMessage;
use App\Models\WarroomThread;
use App\Support\Warroom\MentionParser;
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

    /** ATL-65: published by atlas_ingest, stored as a `news_alert` message in the main thread. */
    public const NEWS_ALERT_EVENT = 'news.alert';

    public const NEWS_ALERT_SOURCE = 'system:news';

    /** ATL-88: live agent run deltas, broadcast on `private-warroom` and never stored. */
    public const THINKING_EVENT = 'warroom.thinking';

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

        if ($stream === 'atlas.warroom' && $envelope['type'] === self::NEWS_ALERT_EVENT) {
            $envelope['payload'] = $this->newsAlertMessage($envelope);
        }

        if ($stream === 'atlas.warroom' && in_array($envelope['type'], [self::MESSAGE_EVENT, self::NEWS_ALERT_EVENT], true)) {
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
        if ($stream === 'atlas.warroom' && $envelope['type'] === self::THINKING_EVENT) {
            $envelope['payload'] = self::capPayload($envelope['payload'], self::thinkingMaxBytes());
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

    private static function thinkingMaxBytes(): int
    {
        $bytes = config('atlas_defaults.warroom.thinking_max_bytes');

        return is_int($bytes) && $bytes > 0 ? $bytes : 16384;
    }

    /**
     * A `warroom.thinking` payload whose JSON is at most $maxBytes long (ATL-89). A payload that
     * already fits is returned unchanged. Otherwise `truncated` is set to true and the longest
     * string fields (`text`, `output`, …; a non-scalar `input` becomes its JSON string first) are
     * cut, at a UTF-8 character boundary, until the JSON fits. If the payload still does not fit
     * with those strings emptied, only its identifying keys are kept. `thread_id` and `reply_to`
     * (ATL-98) are identifying too, so the run's live row always stays under the right question,
     * even in the last-resort shape.
     *
     * @param  array<string, mixed>  $payload
     * @return array<string, mixed>
     */
    public static function capPayload(array $payload, int $maxBytes): array
    {
        if (self::jsonBytes($payload) <= $maxBytes) {
            return $payload;
        }

        $payload['truncated'] = true;
        if (isset($payload['input']) && ! is_scalar($payload['input'])) {
            $payload['input'] = json_encode($payload['input'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE) ?: '';
        }

        // Longest string first, so the bulk (the text or the tool output) is what gets cut.
        // thread_id/reply_to (ATL-98) are identifying, like run_id, and are never cut.
        $strings = array_filter($payload, fn ($value, $key) => is_string($value) && ! in_array($key, ['run_id', 'agent', 'delta_type', 'tool', 'tool_use_id', 'thread_id', 'reply_to'], true), ARRAY_FILTER_USE_BOTH);
        uasort($strings, fn (string $a, string $b) => strlen($b) <=> strlen($a));

        foreach (array_keys($strings) as $key) {
            $full = (string) $payload[$key];
            $payload[$key] = '';
            if (self::jsonBytes($payload) > $maxBytes) {
                continue; // even empty this field does not make room; leave it empty and cut the next
            }
            // Longest prefix (in bytes) that still fits.
            $low = 0;
            $high = strlen($full);
            while ($low < $high) {
                $mid = intdiv($low + $high + 1, 2);
                $payload[$key] = mb_strcut($full, 0, $mid, 'UTF-8');
                if (self::jsonBytes($payload) <= $maxBytes) {
                    $low = $mid;
                } else {
                    $high = $mid - 1;
                }
            }
            $payload[$key] = mb_strcut($full, 0, $low, 'UTF-8');

            return $payload;
        }

        if (self::jsonBytes($payload) <= $maxBytes) {
            return $payload;
        }

        $kept = ['truncated' => true];
        foreach (['run_id', 'agent', 'seq', 'delta_type', 'thread_id', 'reply_to'] as $key) {
            if (array_key_exists($key, $payload) && (is_scalar($payload[$key]) || $payload[$key] === null)) {
                $kept[$key] = is_string($payload[$key]) ? mb_strcut($payload[$key], 0, 128, 'UTF-8') : $payload[$key];
            }
        }

        return $kept;
    }

    /**
     * @param  array<string, mixed>  $payload
     */
    private static function jsonBytes(array $payload): int
    {
        return strlen(json_encode($payload, JSON_INVALID_UTF8_SUBSTITUTE) ?: '');
    }

    /**
     * A `news.alert` envelope (ATL-65) as a `warroom.message` payload: a `system` message in the
     * main thread, authored `system:news`, kind `news_alert`. Every field of the alert is untrusted
     * source text, so it goes into the card as data; content_md is only the headline as plain text
     * (see plainContent), so nothing in it renders as markup.
     *
     * @param  array{id: string, type: string, ts: string, env: 'demo'|'live', source: string, correlation_id: string|null, payload: array<string, mixed>}  $envelope
     * @return array<string, mixed>
     */
    private function newsAlertMessage(array $envelope): array
    {
        if ($envelope['source'] !== self::NEWS_ALERT_SOURCE) {
            throw new MalformedEvent('source is not system:news for a news.alert');
        }

        $alert = $envelope['payload'];
        $this->validate($alert, [
            'news_item_id' => ['required', 'uuid'],
            'cluster_id' => ['required', 'uuid'],
            'headline' => ['required', 'string', 'max:2000'],
            'source' => ['required', 'string', 'max:64'],
            'source_name' => ['required', 'string', 'max:100'],
            'category' => ['nullable', 'string', 'max:32'],
            'url' => ['required', 'string', 'max:4000', 'url:http,https'],
            'published_at' => ['required', 'string', 'date'],
            'importance' => ['required', 'integer', 'between:1,5'],
            'event_type' => ['required', 'string', 'max:32'],
            'inst_ids' => ['present', 'array', 'list'],
            'inst_ids.*' => ['string', 'max:64'],
            'asset_classes' => ['present', 'array', 'list'],
            'asset_classes.*' => ['string', 'max:32'],
            'sentiment' => ['required', 'numeric', 'between:-1,1'],
            'novelty' => ['nullable', 'numeric', 'between:0,1'],
            'reason' => ['required', Rule::in(['importance', 'held_instrument'])],
            'held_inst_ids' => ['present', 'array', 'list'],
            'held_inst_ids.*' => ['string', 'max:64'],
        ], 'payload');

        $keys = [
            'news_item_id', 'cluster_id', 'headline', 'source', 'source_name', 'category', 'url',
            'published_at', 'importance', 'event_type', 'inst_ids', 'asset_classes', 'sentiment',
            'novelty', 'reason', 'held_inst_ids',
        ];
        $card = [];
        foreach ($keys as $key) {
            $card[$key] = $alert[$key] ?? null;
        }
        $card['sentiment'] = (float) $alert['sentiment'];
        $card['novelty'] = isset($alert['novelty']) ? (float) $alert['novelty'] : null;

        return [
            'thread' => ['kind' => 'main', 'key' => WarroomThread::MAIN_KEY],
            'author_type' => 'system',
            'author' => self::NEWS_ALERT_SOURCE,
            'kind' => 'news_alert',
            'content_md' => self::plainContent((string) $alert['headline']),
            'card' => $card,
        ];
    }

    /**
     * The headline as one line of plain text that the War Room's content_md renderer
     * (frontend/src/features/warroom/tokens.ts) shows as itself. That renderer has no links and React
     * escapes HTML; its only markup is `**bold**`, `` `code` ``, `{{TAG}}` status tags, `-`/`1.` list
     * lines and private-use placeholder characters. Those are neutralised here: whitespace folded to
     * one line, control and private-use characters dropped, backticks turned into apostrophes, runs of
     * `*`, `{` or `}` cut to one, and a leading list marker prefixed with U+2060 WORD JOINER.
     */
    public static function plainContent(string $text): string
    {
        $line = (string) preg_replace('/[\x{0000}-\x{001F}\x{007F}\x{E000}-\x{F8FF}]/u', ' ', $text);
        $line = trim((string) preg_replace('/\s+/u', ' ', $line));
        $line = str_replace('`', "'", $line);
        $line = (string) preg_replace(['/\*{2,}/', '/\{{2,}/', '/\}{2,}/'], ['*', '{', '}'], $line);
        if (preg_match('/^(?:-|\d+\.)\s/', $line) === 1) {
            $line = "\u{2060}".$line;
        }

        return $line;
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

        $this->authoriseSource($envelope['source'], $payload['author_type'], $payload['author']);

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
     * `author_type: human` is only ever written by `WarroomController::store`, which inserts the
     * row itself before publishing, so a genuine owner message is always caught by the dedupe
     * check above and never reaches here. Anything that says `human` here was never stored by
     * the API, i.e. it is a forgery (security round 4, ATL-51): reject it. An `agent` or `system`
     * envelope must also say who it claims to be: `source` is `agent:<slug>` with `author` equal
     * to that slug, for a slug in `MentionParser::directory()`, or `system:<name>`.
     */
    private function authoriseSource(string $source, string $authorType, string $author): void
    {
        if ($authorType === 'human') {
            throw new MalformedEvent('payload.author_type "human" is written only by the owner\'s own messages');
        }

        if ($authorType === 'agent') {
            if (
                preg_match('/^agent:([a-z0-9_]+)$/', $source, $matches) !== 1
                || $matches[1] !== $author
                || ! in_array($matches[1], MentionParser::agents(), true)
            ) {
                throw new MalformedEvent('source does not match payload.author for an agent message');
            }

            return;
        }

        // $authorType === 'system' (the only value left in WarroomMessage::AUTHOR_TYPES).
        if (preg_match('/^system:[a-z0-9_]+$/', $source) !== 1) {
            throw new MalformedEvent('source is not a system source for a system message');
        }
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
