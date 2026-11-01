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
 * The owner's Telegram bot (ATL-215, decision #254 (3)); at most one row. The token and chat id are
 * encrypted at rest and never serialised: use TelegramConnectionResource, which only shows the last
 * 4 characters of each.
 *
 * @property string $id UUID
 * @property bool $singleton
 * @property string $bot_token plaintext after the cast
 * @property string $chat_id plaintext after the cast
 * @property string $bot_token_last4
 * @property string $chat_id_last4
 * @property int|null $update_offset highest update_id the poller has taken
 * @property Carbon|null $last_sent_at
 * @property Carbon|null $last_tested_at
 * @property string|null $last_error
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'telegram_connections')]
#[Fillable(['bot_token', 'chat_id', 'bot_token_last4', 'chat_id_last4', 'update_offset', 'last_sent_at', 'last_tested_at', 'last_error'])]
#[Hidden(self::SECRET_FIELDS)]
class TelegramConnection extends Model
{
    use HasUuids;

    /** The columns holding ciphertext. Never log, audit, flash or return them. */
    public const SECRET_FIELDS = ['bot_token', 'chat_id'];

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    public static function current(): ?self
    {
        return self::query()->where('singleton', true)->first();
    }

    /**
     * Audit-safe view: last 4 characters only.
     *
     * @return array<string, string>
     */
    public function auditSnapshot(): array
    {
        return ['bot_token_last4' => $this->bot_token_last4, 'chat_id_last4' => $this->chat_id_last4];
    }

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'bot_token' => 'encrypted',
            'chat_id' => 'encrypted',
            'singleton' => 'boolean',
            'update_offset' => 'integer',
            'last_sent_at' => 'datetime',
            'last_tested_at' => 'datetime',
        ];
    }
}
