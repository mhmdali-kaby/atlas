<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\WarroomThread;
use Carbon\CarbonImmutable;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * A War Room thread with the time of its newest message (null for an empty thread).
 * Unread-agnostic: there is no per-reader state.
 *
 * @mixin WarroomThread
 */
class WarroomThreadResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var WarroomThread $thread */
        $thread = $this->resource;
        $last = $thread->getAttribute('last_message_at');

        return [
            'id' => $thread->id,
            'kind' => $thread->kind,
            'key' => $thread->key,
            'title' => $thread->title,
            'last_message_at' => is_string($last)
                ? CarbonImmutable::parse($last)->utc()->toIso8601ZuluString('microsecond')
                : null,
            'created_at' => $thread->created_at->utc()->toIso8601ZuluString('microsecond'),
        ];
    }
}
