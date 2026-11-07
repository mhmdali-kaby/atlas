<?php

declare(strict_types=1);

namespace App\Casts;

use Illuminate\Contracts\Database\Eloquent\CastsAttributes;
use Illuminate\Database\Eloquent\Model;

/**
 * Casts a Postgres text[] column to a PHP list<string> and back.
 *
 * @implements CastsAttributes<list<string>, mixed>
 */
final class PostgresTextArray implements CastsAttributes
{
    /**
     * @param  array<string, mixed>  $attributes
     * @return list<string>
     */
    public function get(Model $model, string $key, mixed $value, array $attributes): array
    {
        if (! is_string($value)) {
            return [];
        }

        return self::parse($value);
    }

    /**
     * @param  array<string, mixed>  $attributes
     */
    public function set(Model $model, string $key, mixed $value, array $attributes): string
    {
        if (! is_array($value)) {
            throw new \InvalidArgumentException("[{$key}] must be a list of strings.");
        }

        return self::serialize(array_values(array_map(static fn (mixed $item): string => is_scalar($item)
            ? (string) $item
            : throw new \InvalidArgumentException("[{$key}] must be a list of strings."), $value)));
    }

    /**
     * @param  list<string>  $items
     */
    public static function serialize(array $items): string
    {
        $quoted = array_map(
            static fn (string $item): string => '"'.str_replace(['\\', '"'], ['\\\\', '\\"'], $item).'"',
            $items,
        );

        return '{'.implode(',', $quoted).'}';
    }

    /**
     * Parses a one-dimensional Postgres array literal such as {a,"b c","d\"e"}.
     *
     * @return list<string>
     */
    public static function parse(string $literal): array
    {
        $inner = substr(trim($literal), 1, -1);
        if ($inner === '') {
            return [];
        }

        $items = [];
        $current = '';
        $quoted = false;
        $length = strlen($inner);
        for ($i = 0; $i < $length; $i++) {
            $char = $inner[$i];
            if ($quoted) {
                if ($char === '\\' && $i + 1 < $length) {
                    $current .= $inner[++$i];
                } elseif ($char === '"') {
                    $quoted = false;
                } else {
                    $current .= $char;
                }

                continue;
            }
            if ($char === '"') {
                $quoted = true;
            } elseif ($char === ',') {
                $items[] = $current;
                $current = '';
            } else {
                $current .= $char;
            }
        }
        $items[] = $current;

        return $items;
    }
}
