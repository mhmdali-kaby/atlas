<?php

declare(strict_types=1);

namespace App\Support\System;

/**
 * The env trading_core runs for (ATLAS_ENV, decision #235): the owner's kill switch, cancels and
 * closes act on it, and the orders list shows it. Until the live switch exists (Phase 4) it is the
 * configured default env.
 */
final class ActiveEnv
{
    public static function get(): string
    {
        $env = config('atlas_defaults.env.default');

        return $env === 'live' ? 'live' : 'demo';
    }
}
