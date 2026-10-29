<?php

declare(strict_types=1);

namespace App\Http\Requests\Warroom;

use Illuminate\Validation\Rule;

/**
 * POST /api/warroom/messages — an owner message.
 * { "thread_id": uuid, "content_md": 1..4000 chars, "reply_to": uuid|null (same thread), "env"?: demo|live }
 */
class StoreMessageRequest extends WarroomRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        $threadId = $this->input('thread_id');

        return [
            ...parent::rules(),
            'thread_id' => [
                'required', 'uuid',
                Rule::exists('warroom_threads', 'id')->where('env', $this->env()),
            ],
            'content_md' => ['required', 'string', 'max:'.self::intDefault('content_max_chars', 4000)],
            'reply_to' => [
                'sometimes', 'nullable', 'uuid',
                Rule::exists('warroom_messages', 'id')
                    ->where('thread_id', is_string($threadId) ? $threadId : '')
                    ->where('env', $this->env()),
            ],
        ];
    }

    public function threadId(): string
    {
        return (string) $this->validated('thread_id');
    }

    public function contentMd(): string
    {
        return (string) $this->validated('content_md');
    }

    public function replyTo(): ?string
    {
        $replyTo = $this->validated('reply_to');

        return is_string($replyTo) ? $replyTo : null;
    }
}
