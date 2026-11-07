<?php

declare(strict_types=1);

namespace App\Rules;

use Carbon\CarbonImmutable;
use Closure;
use Illuminate\Contracts\Validation\ValidationRule;

/**
 * A strict ISO 8601 date or date-time: "2026-10-29", "2026-10-29T08:00", "2026-10-29T08:00:00Z",
 * "2026-10-29T08:00:00.123+03:00". Unlike Laravel's `date` rule (strtotime), relative phrases
 * ("tomorrow"), "@<unix>" and rolled-over calendar dates ("2026-02-30") are refused, so nothing
 * Postgres would reject can reach a query. A value without an offset is read as UTC.
 */
final class IsoDateTime implements ValidationRule
{
    private const PATTERN = '/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?(Z|[+-]\d{2}:?\d{2})?)?$/';

    public function validate(string $attribute, mixed $value, Closure $fail): void
    {
        if (self::parse($value) === null) {
            $fail(__('news.invalid_datetime', ['attribute' => $attribute]));
        }
    }

    public static function parse(mixed $value): ?CarbonImmutable
    {
        if (! is_string($value) || preg_match(self::PATTERN, $value, $m) !== 1) {
            return null;
        }

        [$year, $month, $day] = [(int) $m[1], (int) $m[2], (int) $m[3]];
        if ($year < 1970 || ! checkdate($month, $day, $year)) {
            return null;
        }
        if ((int) ($m[4] ?? 0) > 23 || (int) ($m[5] ?? 0) > 59 || (int) ($m[6] ?? 0) > 59) {
            return null;
        }

        $hasOffset = isset($m[7]);

        try {
            return CarbonImmutable::parse($value, $hasOffset ? null : 'UTC')->utc();
        } catch (\Throwable) {
            return null;
        }
    }
}
