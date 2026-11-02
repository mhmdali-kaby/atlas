<?php

declare(strict_types=1);

namespace App\Support\System;

use App\Models\SystemState;
use Carbon\CarbonImmutable;

/**
 * The first live month (decision #255 (6)): `first_month_days` (30) from system_state.live_started_at.
 * Inside it, live autonomy above `max_live_autonomy` (2) is refused and the live bot capital cap
 * cannot be cleared. Before the first switch to live there is no month (live_started_at is null).
 */
final class FirstLiveMonth
{
    public static function endsAt(): ?CarbonImmutable
    {
        $started = SystemState::query()->find(SystemState::ID)?->live_started_at;
        if ($started === null) {
            return null;
        }
        $days = config('atlas_defaults.env.live_preflight.first_month_days');

        return CarbonImmutable::instance($started)->addDays(is_int($days) ? $days : 30);
    }

    public static function active(?CarbonImmutable $now = null): bool
    {
        $ends = self::endsAt();

        return $ends !== null && ($now ?? CarbonImmutable::now())->lessThan($ends);
    }

    public static function maxAutonomy(): int
    {
        $max = config('atlas_defaults.env.live_preflight.max_live_autonomy');

        return is_int($max) ? $max : 2;
    }
}
