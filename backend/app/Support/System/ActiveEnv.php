<?php

declare(strict_types=1);

namespace App\Support\System;

use Illuminate\Support\Facades\DB;

/**
 * The env Atlas trades in (decision #255 (4)): `system_state.active_env`, written only by the guarded
 * switch (POST /api/environment/switch, ATL-236). The owner's kill switch, cancels and closes act on
 * it, and the orders list shows it. The configured default env is used only while the row is missing.
 */
final class ActiveEnv
{
    public static function get(): string
    {
        $stored = DB::table('system_state')->where('id', 1)->value('active_env');
        $env = is_string($stored) ? $stored : config('atlas_defaults.env.default');

        return $env === 'live' ? 'live' : 'demo';
    }
}
