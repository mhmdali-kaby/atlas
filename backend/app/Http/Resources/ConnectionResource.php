<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\Connection;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * A connection as the API returns it: never a secret value, only the key's last 4 characters
 * and whether a secret and passphrase are stored (brief §1.6).
 *
 * @mixin Connection
 */
class ConnectionResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var Connection $connection */
        $connection = $this->resource;

        return [
            'id' => $connection->id,
            'exchange' => $connection->exchange,
            'env' => $connection->env,
            'label' => $connection->label,
            'api_key_last4' => $connection->apiKeyLast4(),
            'has_secret' => $connection->api_secret !== '',
            'has_passphrase' => $connection->passphrase !== '',
            'permissions' => $connection->permissions,
            'account_mode' => $connection->account_mode,
            'position_mode' => $connection->position_mode,
            'uid_last4' => $connection->uid_last4,
            'last_verified_at' => $connection->last_verified_at?->toIso8601ZuluString('microsecond'),
            'last_test_error' => $connection->last_test_error,
            'created_at' => $connection->created_at->toIso8601ZuluString('microsecond'),
            'updated_at' => $connection->updated_at->toIso8601ZuluString('microsecond'),
        ];
    }
}
