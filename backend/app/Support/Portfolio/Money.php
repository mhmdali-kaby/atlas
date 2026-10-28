<?php

declare(strict_types=1);

namespace App\Support\Portfolio;

use Brick\Math\BigDecimal;
use Brick\Math\RoundingMode;

/**
 * Decimal helpers for the Portfolio API. Every amount travels as a decimal string, never a float.
 */
final class Money
{
    /** Currencies valued at 1 USD in Phase 0 (no FX conversion yet). */
    public const USD_STABLE = ['USDT', 'USDC', 'USD'];

    /** Scale of percentage outputs (pct_of_portfolio, total_pnl_pct, upl_pct). */
    public const PCT_SCALE = 4;

    public static function of(mixed $value): ?BigDecimal
    {
        if ($value === null || $value === '') {
            return null;
        }

        return BigDecimal::of(is_int($value) ? $value : (string) (is_scalar($value) ? $value : ''));
    }

    public static function str(?BigDecimal $value): ?string
    {
        if ($value === null) {
            return null;
        }

        $stripped = $value->strippedOfTrailingZeros();

        // Never emit "-0".
        return $stripped->isZero() ? '0' : $stripped->toString();
    }

    public static function isUsdStable(?string $ccy): bool
    {
        return $ccy !== null && in_array(strtoupper($ccy), self::USD_STABLE, true);
    }

    /**
     * $part / $whole × 100 at PCT_SCALE, or null when $whole is not positive.
     */
    public static function pct(BigDecimal $part, BigDecimal $whole): ?BigDecimal
    {
        if ($whole->isNegativeOrZero()) {
            return null;
        }

        return $part->multipliedBy(100)->dividedBy($whole, self::PCT_SCALE, RoundingMode::HalfUp);
    }
}
