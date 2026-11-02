<?php

declare(strict_types=1);

namespace App\Http\Requests\Notifications;

use Illuminate\Foundation\Http\FormRequest;

/**
 * PUT /api/notifications/telegram (ATL-215): the bot token from @BotFather and the owner's chat id.
 * Both are required; the row is replaced as a whole.
 *
 * ATL-221 (security round 16): chat_id must be a positive id, i.e. a private chat with the owner.
 * Telegram group and supergroup chat ids are negative; accepting one would let every member of that
 * group press the Approve/Reject buttons, since TelegramPoller only checked the chat, not the sender.
 */
class StoreTelegramRequest extends FormRequest
{
    /**
     * @return array<string, list<string>>
     */
    public function rules(): array
    {
        return [
            // <bot id>:<secret>, as BotFather issues it.
            'bot_token' => ['required', 'string', 'max:128', 'regex:/^\d{3,20}:[A-Za-z0-9_-]{20,100}$/'],
            // A positive numeric chat id: Telegram's user id for a private chat with the bot.
            'chat_id' => ['required', 'string', 'max:32', 'regex:/^\d{1,20}$/'],
        ];
    }

    public function botToken(): string
    {
        return (string) $this->validated('bot_token');
    }

    public function chatId(): string
    {
        return (string) $this->validated('chat_id');
    }
}
