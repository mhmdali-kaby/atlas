<?php

declare(strict_types=1);

namespace App\Http\Requests\Journal;

use Carbon\CarbonImmutable;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Pagination\Cursor;
use Illuminate\Support\Str;
use Illuminate\Validation\Validator;

/**
 * Shared helpers for the read-only journal, scores and notebook requests (ATL-200).
 */
abstract class JournalApiRequest extends FormRequest
{
    public function authorize(): bool
    {
        return $this->user() !== null;
    }

    protected static function int(string $key, int $default): int
    {
        $value = config('atlas_defaults.journal_api.'.$key);

        return is_int($value) ? $value : $default;
    }

    public static function timezone(): string
    {
        $tz = config('atlas_defaults.timezone.display');

        return is_string($tz) ? $tz : 'Asia/Baghdad';
    }

    protected static function text(mixed $value): ?string
    {
        return is_string($value) && $value !== '' ? $value : null;
    }

    /**
     * A crafted `(created_at, id)` cursor would otherwise reach Postgres as a literal and fail
     * there (a 500), so it is a 422 instead.
     *
     * @return callable(Validator): void
     */
    protected function cursorRule(): callable
    {
        return function (Validator $validator): void {
            $encoded = $this->input('cursor');
            if ($validator->errors()->has('cursor') || ! is_string($encoded) || $encoded === '') {
                return;
            }
            if (! self::validCursor($encoded)) {
                $validator->errors()->add('cursor', __('journal.invalid_cursor'));
            }
        };
    }

    private static function validCursor(string $encoded): bool
    {
        $params = Cursor::fromEncoded($encoded)?->toArray();
        if (! is_array($params)) {
            return false;
        }
        unset($params['_pointsToNextItems']);
        $keys = array_keys($params);
        sort($keys);
        if ($keys !== ['created_at', 'id']) {
            return false;
        }
        $id = $params['id'];
        $ts = $params['created_at'];

        return is_string($id) && Str::isUuid($id)
            && is_string($ts)
            && preg_match('/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?[+-]\d{2}(:\d{2})?$/', $ts) === 1
            && CarbonImmutable::canBeCreatedFromFormat(substr($ts, 0, 19), 'Y-m-d H:i:s')
            && substr($ts, 0, 4) !== '0000';
    }

    public function limit(): int
    {
        $limit = $this->validated('limit');

        return is_numeric($limit) ? (int) $limit : self::int('per_page_default', 50);
    }
}
