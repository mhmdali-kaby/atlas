<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\TelegramConnection;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * The Telegram bot as the API shows it (ATL-215): never the token or chat id, only their last 4
 * characters. A missing row is {configured: false}.
 *
 * @property TelegramConnection|null $resource
 */
class TelegramConnectionResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        $c = $this->resource;
        if (! $c instanceof TelegramConnection) {
            return ['configured' => false, 'bot_token_last4' => null, 'chat_id_last4' => null, 'last_sent_at' => null, 'last_tested_at' => null, 'last_error' => null, 'updated_at' => null];
        }

        return [
            'configured' => true,
            'bot_token_last4' => $c->bot_token_last4,
            'chat_id_last4' => $c->chat_id_last4,
            'last_sent_at' => $c->last_sent_at?->utc()->toIso8601ZuluString('microsecond'),
            'last_tested_at' => $c->last_tested_at?->utc()->toIso8601ZuluString('microsecond'),
            'last_error' => $c->last_error,
            'updated_at' => $c->updated_at->utc()->toIso8601ZuluString('microsecond'),
        ];
    }
}
