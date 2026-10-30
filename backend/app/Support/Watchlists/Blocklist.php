<?php

declare(strict_types=1);

namespace App\Support\Watchlists;

use App\Models\Setting;

/**
 * The owner's instrument blocklist for an env, read the same way Market Scout's universe filter
 * reads it (atlas_agents.scout.universe.config_from): the settings row's `blocklist` list when
 * it has one, else config agents.scout.universe.blocklist. Matching is case-insensitive.
 */
final class Blocklist
{
    /**
     * @return list<string> upper-case instIds
     */
    public function for(string $env): array
    {
        $data = Setting::query()->where('env', $env)->value('data');
        $data = is_string($data) ? json_decode($data, true) : $data;
        $list = is_array($data) ? ($data['blocklist'] ?? null) : null;
        if (! self::isStringList($list)) {
            $list = config('atlas_defaults.agents.scout.universe.blocklist');
        }

        return self::isStringList($list) ? array_map('strtoupper', $list) : [];
    }

    public function contains(string $env, string $instId): bool
    {
        return in_array(strtoupper($instId), $this->for($env), true);
    }

    /**
     * @phpstan-assert-if-true list<string> $value
     */
    private static function isStringList(mixed $value): bool
    {
        return is_array($value) && array_is_list($value)
            && array_filter($value, fn (mixed $v): bool => ! is_string($v)) === [];
    }
}
