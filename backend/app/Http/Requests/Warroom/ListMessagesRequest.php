<?php

declare(strict_types=1);

namespace App\Http\Requests\Warroom;

use DateTimeImmutable;
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
     * ATL-52 (round 4, low #3 follow-up): strtotime() accepts relative phrases ("+1 week"),
     * timezone-qualified phrases ("tomorrow noon Europe/Paris"), unix-timestamp syntax
     * ("@99999999999999") and silently rolls over invalid calendar dates ("2026-02-30"), so all
     * of those passed this check and reached Postgres as literal timestamps, which rejects them
     * with SQLSTATE 22008/22007 (a 500). Laravel's cursor paginator always builds created_at with
     * AbstractCursorPaginator::ensureParameterIsPrimitive(), i.e. Carbon's plain (string) cast
     * ("Y-m-d H:i:s", see CURSOR_TIMESTAMP_FORMAT) — parse exactly that format, with no rollover
     * and no trailing data allowed.
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
                    && is_string($createdAt) && self::isStrictCursorTimestamp($createdAt);

                if (! $valid) {
                    $validator->errors()->add('cursor', __('warroom.invalid_cursor'));
                }
            },
        ];
    }

    /**
     * The exact wire format the cursor paginator produces for created_at, e.g.
     * "2026-10-29 05:43:27". createFromFormat() with the leading "!" resets every field not
     * present in the format (so there is no inherited "now"), and DateTimeImmutable rejects
     * trailing or missing characters outright rather than guessing.
     */
    private const CURSOR_TIMESTAMP_FORMAT = '!Y-m-d H:i:s';

    private static function isStrictCursorTimestamp(string $value): bool
    {
        $date = DateTimeImmutable::createFromFormat(self::CURSOR_TIMESTAMP_FORMAT, $value);
        if ($date === false) {
            return false;
        }

        // createFromFormat() silently rolls 2026-02-30 forward to 2026-03-02 instead of failing;
        // getLastErrors() is the only signal that the calendar date it accepted was invalid.
        $errors = DateTimeImmutable::getLastErrors();

        return $errors === false || ($errors['warning_count'] === 0 && $errors['error_count'] === 0);
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
