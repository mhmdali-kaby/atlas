<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\WarroomMessage;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * The one JSON shape of a War Room message, used by the War Room API and the Reverb bridge.
 * cost_usd is a decimal string (never a float); created_at is UTC ISO-8601 with microseconds.
 * unmatched_numbers is the number guard's list from card meta, or null (ATL-89).
 *
 * @mixin WarroomMessage
 */
class WarroomMessageResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var WarroomMessage $message */
        $message = $this->resource;

        return [
            'id' => $message->id,
            'thread_id' => $message->thread_id,
            'author_type' => $message->author_type,
            'author' => $message->author,
            'kind' => $message->kind,
            'content_md' => $message->content_md,
            'card' => $message->card,
            'reply_to' => $message->reply_to,
            'mentions' => $message->mentions,
            'reasoning_md' => $message->reasoning_md,
            'tool_calls' => $message->tool_calls,
            'tokens_in' => $message->tokens_in,
            'tokens_out' => $message->tokens_out,
            'cost_usd' => $message->cost_usd,
            'unmatched_numbers' => self::unmatchedNumbers($message->card),
            'created_at' => $message->created_at->utc()->toIso8601ZuluString('microsecond'),
        ];
    }

    /**
     * The number guard's unmatched numbers (ATL-91 `to_meta()`), read from the card's meta:
     * `card.meta.numguard.unmatched_numbers`, or `card.meta.unmatched_numbers`. Null when the
     * message has no guard result (owner and system messages, or a card without meta).
     *
     * @param  array<string, mixed>|null  $card
     * @return list<mixed>|null
     */
    private static function unmatchedNumbers(?array $card): ?array
    {
        $meta = $card['meta'] ?? null;
        if (! is_array($meta)) {
            return null;
        }
        $guard = $meta['numguard'] ?? null;
        $numbers = is_array($guard) && array_key_exists('unmatched_numbers', $guard)
            ? $guard['unmatched_numbers']
            : ($meta['unmatched_numbers'] ?? null);

        return is_array($numbers) && array_is_list($numbers) ? $numbers : null;
    }
}
