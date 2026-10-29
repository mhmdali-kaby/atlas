<?php

declare(strict_types=1);

namespace App\Http\Requests\Warroom;

use Illuminate\Pagination\Cursor;
use Illuminate\Support\Str;
use Illuminate\Validation\Rule;
use Illuminate\Validation\Validator;

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

    /**
     * security-audit-round-4 #3: WarroomController::messages() orders by created_at then id, so
     * cursorPaginate() needs both parameters in the cursor. A cursor missing either one, or with
     * the wrong shape, throws UnexpectedValueException deep inside pagination (a 500) instead of
     * failing validation here. Reject it as 422 before the controller ever sees it.
     *
     * @return list<callable(Validator): void>
     */
    public function after(): array
    {
        return [
            function (Validator $validator): void {
                $encoded = $this->input('cursor');
                if ($encoded === null || $validator->errors()->has('cursor')) {
                    return;
                }

                $cursor = Cursor::fromEncoded($encoded);
                $parameters = $cursor?->toArray() ?? [];
                $id = $parameters['id'] ?? null;
                $createdAt = $parameters['created_at'] ?? null;

                $valid = $cursor !== null
                    && is_string($id) && Str::isUuid($id)
                    && is_string($createdAt) && strtotime($createdAt) !== false;

                if (! $valid) {
                    $validator->errors()->add('cursor', __('warroom.invalid_cursor'));
                }
            },
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
