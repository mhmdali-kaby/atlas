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
            'updated_at' => $setting->updated_at->toIso8601ZuluString('microsecond'),
        ];
    }
}
