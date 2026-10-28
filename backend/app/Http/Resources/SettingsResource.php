<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\Setting;
use App\Support\Settings\SettingsSchema;
use App\Support\Settings\SettingsService;
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
            'updated_at' => $setting->updated_at->toIso8601ZuluString('microsecond'),
        ];
    }
}
