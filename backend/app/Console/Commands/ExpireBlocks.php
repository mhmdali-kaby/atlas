<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Models\Setting;
use App\Support\Settings\SettingsSchema;
use App\Support\Settings\SettingsService;
use App\Support\Settings\StaleSettingsVersion;
use Carbon\CarbonImmutable;
use Illuminate\Console\Attributes\Description;
use Illuminate\Console\Attributes\Signature;
use Illuminate\Console\Command;

/**
 * ATL-163: lifts blocklist entries whose `blocklist_until` time has passed (the composer's
 * `/block <instId> today`). The change goes through SettingsService like an owner edit, so it is
 * versioned, audited and published as system.settings_changed; every reader of the blocklist
 * (Risk Engine, Market Scout, pin refusal) then sees the instrument unblocked. Scheduled every
 * minute (routes/console.php).
 */
#[Signature('atlas:expire-blocks')]
#[Description('Remove blocklist entries whose expiry has passed')]
class ExpireBlocks extends Command
{
    public function handle(SettingsService $settings): int
    {
        $now = CarbonImmutable::now('UTC');

        foreach (Setting::query()->orderBy('env')->get() as $setting) {
            $values = $settings->values($setting);
            $until = $values[SettingsSchema::BLOCKLIST_UNTIL_FIELD] ?? [];
            $blocked = $values['blocklist'] ?? [];
            if (! is_array($until) || ! is_array($blocked) || $until === []) {
                continue;
            }

            $expired = array_keys(array_filter(
                $until,
                fn (string $at): bool => ! CarbonImmutable::parse($at)->isAfter($now),
            ));
            if ($expired === []) {
                continue;
            }
            $expired = array_map(strval(...), $expired);

            try {
                $settings->update($setting->env, $setting->version, [
                    'blocklist' => array_values(array_filter($blocked, fn (mixed $id): bool => ! in_array($id, $expired, true))),
                    SettingsSchema::BLOCKLIST_UNTIL_FIELD => array_diff_key($until, array_flip($expired)),
                ]);
                $this->components->info(sprintf('%s: unblocked %s', $setting->env, implode(', ', $expired)));
            } catch (StaleSettingsVersion) {
                // Changed while we read it; the next run (a minute later) sees the new version.
                $this->components->warn("{$setting->env}: settings changed meanwhile; retrying next run.");
            }
        }

        return self::SUCCESS;
    }
}
