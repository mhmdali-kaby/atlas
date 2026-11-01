<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\Notification;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * One notification (ATL-214): the GET /api/notifications row and the `notification.created`
 * broadcast payload on private-system. title_key + params are rendered by the SPA's i18n; there
 * is no English text here.
 *
 * @property Notification $resource
 */
class NotificationResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        $n = $this->resource;

        return [
            'id' => $n->id,
            'env' => $n->env,
            'event' => $n->event,
            'severity' => $n->severity,
            'title_key' => $n->title_key,
            'params' => (object) $n->params,
            'link' => $n->link,
            'channels' => (object) $n->channels,
            'source_event_id' => $n->source_event_id,
            'read_at' => $n->read_at?->utc()->format('Y-m-d\TH:i:s.u\Z'),
            'created_at' => $n->created_at->utc()->format('Y-m-d\TH:i:s.u\Z'),
        ];
    }
}
