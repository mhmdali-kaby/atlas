<?php

declare(strict_types=1);

namespace App\Support\Proposals;

use Brick\Math\BigDecimal;
use Brick\Math\Exception\MathException;

/** Decimal strings for prices and sizes (brief §21: brick/math, never floats). */
final class Dec
{
    /** "4000.000000000000000000" → "4000"; null stays null; garbage → null. */
    public static function out(mixed $value): ?string
    {
        if ($value === null || $value === '') {
            return null;
        }
        try {
            return (string) BigDecimal::of(self::str($value))->strippedOfTrailingZeros();
        } catch (MathException) {
            return null;
        }
    }

    public static function of(mixed $value): ?BigDecimal
    {
        if ($value === null || $value === '' || is_bool($value) || ! (is_string($value) || is_int($value) || is_float($value))) {
            return null;
        }
        try {
            return BigDecimal::of(self::str($value));
        } catch (MathException) {
            return null;
        }
    }

    private static function str(mixed $value): string
    {
        // A JSON number arrives as int or float; its shortest decimal form is what the owner typed.
        if (is_float($value)) {
            return rtrim(rtrim(sprintf('%.15F', $value), '0'), '.');
        }

        return is_scalar($value) ? (string) $value : '';
    }
}
