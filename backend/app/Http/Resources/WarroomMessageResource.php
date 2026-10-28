<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\WarroomMessage;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * The one JSON shape of a War Room message, used by the War Room API and the Reverb bridge.
 * cost_usd is a decimal string (never a float); created_at is UTC ISO-8601 with microseconds.
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
            'created_at' => $message->created_at->utc()->toIso8601ZuluString('microsecond'),
        ];
    }
}
