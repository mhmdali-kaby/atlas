<?php

declare(strict_types=1);

namespace App\Support\Data;

use Illuminate\Support\Facades\DB;

/**
 * On-disk size of each market-data hypertable (ATL-48), from TimescaleDB's hypertable_size()
 * (table + indexes + TOAST across all chunks). Postgres only.
 */
final class StorageReport
{
    /** The ATL-44 hypertables, in the order Settings → Data lists them. */
    public const HYPERTABLES = ['candles', 'funding_rates', 'open_interest', 'long_short_ratio', 'taker_volume'];

    /**
     * @return array{hypertables: list<array{name: string, bytes: int}>, total_bytes: int}
     */
    public function build(): array
    {
        $rows = [];
        $total = 0;
        foreach (self::HYPERTABLES as $name) {
            $value = DB::scalar('SELECT COALESCE(hypertable_size(?::regclass), 0)', [$name]);
            $bytes = is_numeric($value) ? (int) $value : 0;
            $rows[] = ['name' => $name, 'bytes' => $bytes];
            $total += $bytes;
        }

        return ['hypertables' => $rows, 'total_bytes' => $total];
    }
}
