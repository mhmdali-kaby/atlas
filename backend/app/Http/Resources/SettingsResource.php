<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\Setting;
use App\Support\Settings\SettingsSchema;
use App\Support\Settings\SettingsService;
use App\Support\System\FirstLiveMonth;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * @mixin Setting
 */
class SettingsResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var Setting $setting */
        $setting = $this->resource;

        return [
            'id' => $setting->id,
            'env' => $setting->env,
            'version' => $setting->version,
            'settings' => app(SettingsService::class)->values($setting),
            'presets' => SettingsSchema::presets(),
            // ATL-33 AC2: the SPA's AutonomyPicker disables levels above this without guessing it.
            'max_enabled_autonomy_level' => (int) config('atlas_defaults.autonomy.max_enabled_level'),
            // ATL-238, decision #255 (6): while the first live month runs, the end of it (live
            // autonomy capped, live capital cap required); null otherwise and always for demo.
            'first_live_month_ends_at' => $setting->env === 'live' && FirstLiveMonth::active()
                ? FirstLiveMonth::endsAt()?->toIso8601ZuluString('microsecond')
                : null,
            'max_live_autonomy_level' => FirstLiveMonth::maxAutonomy(),
            'updated_at' => $setting->updated_at->toIso8601ZuluString('microsecond'),
        ];
    }
}
