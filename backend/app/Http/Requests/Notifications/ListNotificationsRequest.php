<?php

declare(strict_types=1);

namespace App\Http\Requests\Notifications;

use App\Http\Requests\Journal\JournalApiRequest;
use Illuminate\Validation\Validator;

/**
 * GET /api/notifications?cursor=&limit=&unread=1 (ATL-214). Reuses the journal's (created_at, id)
 * cursor check, so a crafted cursor is a 422, not a 500.
 */
class ListNotificationsRequest extends JournalApiRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'cursor' => ['sometimes', 'nullable', 'string', 'max:1024'],
            'limit' => ['sometimes', 'integer', 'min:1', 'max:'.self::setting('per_page_max', 200)],
            'unread' => ['sometimes', 'in:0,1,true,false'],
        ];
    }

    /**
     * @return list<callable(Validator): void>
     */
    public function after(): array
    {
        return [$this->cursorRule()];
    }

    public function unreadOnly(): bool
    {
        return in_array($this->validated('unread'), ['1', 'true', 1, true], true);
    }

    public function limit(): int
    {
        $limit = $this->validated('limit');

        return is_numeric($limit) ? (int) $limit : self::setting('per_page_default', 50);
    }

    private static function setting(string $key, int $default): int
    {
        $value = config('atlas_defaults.notifications.'.$key);

        return is_int($value) ? $value : $default;
    }
}
