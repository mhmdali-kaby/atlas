<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Hidden;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * One exchange API connection per (exchange, env) (brief §14.2). The three secrets are stored
 * with Laravel `encrypted` casts and are never serialised: use ConnectionResource, which only
 * exposes the key's last 4 characters.
 *
 * @property string $id UUID
 * @property string $exchange
 * @property 'demo'|'live' $env
 * @property string $label
 * @property string $api_key plaintext after the cast
 * @property string $api_secret plaintext after the cast
 * @property string $passphrase plaintext after the cast
 * @property array<string, mixed>|list<string>|null $permissions
 * @property string|null $account_mode
 * @property string|null $position_mode
 * @property string|null $uid_last4
 * @property Carbon|null $last_verified_at
 * @property string|null $last_test_error
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'connections')]
#[Fillable(['exchange', 'env', 'label', 'api_key', 'api_secret', 'passphrase', 'permissions', 'account_mode', 'position_mode', 'uid_last4', 'last_verified_at', 'last_test_error'])]
#[Hidden(self::SECRET_FIELDS)]
class Connection extends Model
{
    use HasUuids;

    /** The columns holding ciphertext. Never log, audit, flash or return them. */
    public const SECRET_FIELDS = ['api_key', 'api_secret', 'passphrase'];

    /** Columns that describe the verified key; cleared when any secret changes. */
    public const VERIFICATION_FIELDS = ['permissions', 'account_mode', 'position_mode', 'uid_last4', 'last_verified_at', 'last_test_error'];

    public function apiKeyLast4(): string
    {
        return mb_substr($this->api_key, -4);
    }

    /**
     * Audit-safe view of the row: no secret values, only the key's last 4 characters.
     *
     * @return array<string, mixed>
     */
    public function auditSnapshot(): array
    {
        return [
            'exchange' => $this->exchange,
            'env' => $this->env,
            'label' => $this->label,
            'api_key_last4' => $this->apiKeyLast4(),
        ];
    }

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'api_key' => 'encrypted',
            'api_secret' => 'encrypted',
            'passphrase' => 'encrypted',
            'permissions' => 'array',
            'last_verified_at' => 'datetime',
        ];
    }
}
