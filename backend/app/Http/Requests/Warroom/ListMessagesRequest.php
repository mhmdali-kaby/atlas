<?php

declare(strict_types=1);

namespace App\Http\Requests\Warroom;

use Illuminate\Validation\Rule;

/**
 * GET /api/warroom/messages?thread_id=&cursor=&limit=&env=
 * thread_id must be a thread of the env; limit is 1..warroom.page_limit_max.
 */
class ListMessagesRequest extends WarroomRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            ...parent::rules(),
            'thread_id' => [
                'required', 'uuid',
                Rule::exists('warroom_threads', 'id')->where('env', $this->env()),
            ],
            'cursor' => ['sometimes', 'nullable', 'string', 'max:1024'],
            'limit' => ['sometimes', 'integer', 'min:1', 'max:'.self::intDefault('page_limit_max', 100)],
        ];
    }

    public function threadId(): string
    {
        return (string) $this->validated('thread_id');
    }

    public function limit(): int
    {
        $limit = $this->validated('limit');

        return is_numeric($limit) ? (int) $limit : self::intDefault('page_limit_default', 50);
    }
}
