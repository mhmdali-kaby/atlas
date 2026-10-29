<?php

declare(strict_types=1);

namespace App\Support\News;

use Brick\Math\BigDecimal;
use Carbon\CarbonImmutable;

/**
 * Output formatting for raw query-builder values in the news API.
 */
final class NewsFormat
{
    /** Postgres timestamptz text → UTC ISO 8601 with a Z suffix. */
    public static function timestamp(mixed $value): ?string
    {
        if (! is_string($value) || $value === '') {
            return null;
        }

        return CarbonImmutable::parse($value)->utc()->toIso8601ZuluString();
    }

    /** Scores in a bounded range (sentiment, novelty): JSON numbers, not money. */
    public static function float(mixed $value): ?float
    {
        return is_numeric($value) ? (float) $value : null;
    }

    /** numeric(38,18) → shortest exact decimal string ("1.230000…" → "1.23"). */
    public static function decimal(mixed $value): ?string
    {
        if (! is_numeric($value)) {
            return null;
        }

        return BigDecimal::of((string) $value)->strippedOfTrailingZeros()->toString();
    }
}
