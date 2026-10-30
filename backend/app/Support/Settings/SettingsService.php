<?php

declare(strict_types=1);

namespace App\Support\Settings;

use App\Models\Setting;
use App\Models\SettingsHistory;
use App\Models\User;
use App\Support\Auditable;
use App\Support\Events\EventBus;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Support\Facades\DB;
use Throwable;

/**
 * The only writer of the settings tables (brief §3.2, §14): every change bumps the version,
 * writes a settings_history row and an audit_log row, then publishes
 * system.settings_changed on atlas.system.
 */
final class SettingsService
{
    public const EVENT_STREAM = 'atlas.system';

    public const EVENT_TYPE = 'system.settings_changed';

    public const EVENT_SOURCE = 'backend:settings';

    public function __construct(
        private readonly Auditable $audit,
        private readonly EventBus $events,
    ) {}

    /**
     * The settings row for an env, created at version 1 from the defaults on first read.
     */
    public function current(string $env, ?User $user = null): Setting
    {
        $existing = Setting::query()->where('env', $env)->first();
        if ($existing !== null) {
            return $existing;
        }

        try {
            return DB::transaction(function () use ($env, $user): Setting {
                $data = SettingsSchema::defaults();
                $setting = new Setting(['env' => $env, 'version' => 1]);
                $this->fill($setting, $data);
                $setting->save();

                $this->writeHistory($setting, $this->diff([], $data), $user);
                $this->audit->record('settings.created', 'settings', $setting->id, null, ['env' => $env, 'version' => 1, 'data' => $data], $user);

                return $setting;
            });
        } catch (UniqueConstraintViolationException) {
            // Another request created it first.
            return Setting::query()->where('env', $env)->firstOrFail();
        }
    }

    /**
     * Current values with any field added since the row was saved filled from the defaults.
     *
     * @return array<string, bool|int|string|list<string>|null>
     */
    public function values(Setting $setting): array
    {
        $values = [];
        $defaults = SettingsSchema::defaults();
        foreach (SettingsSchema::fields() as $field) {
            // array_key_exists, not ??: a stored null (no bot capital cap) is a value, not a gap.
            $values[$field] = array_key_exists($field, $setting->data) ? $setting->data[$field] : $defaults[$field];
        }

        return $values;
    }

    /**
     * Applies validated changes on top of $version.
     *
     * @param  array<string, bool|int|string|list<string>|null>  $changes  validated by UpdateSettingsRequest
     *
     * @throws StaleSettingsVersion
     */
    public function update(string $env, int $version, array $changes, ?User $user = null): Setting
    {
        $this->current($env, $user);

        /** @var array{0: Setting, 1: array<string, array{from: bool|int|string|list<string>|null, to: bool|int|string|list<string>|null}>} $result */
        $result = DB::transaction(function () use ($env, $version, $changes, $user): array {
            $setting = Setting::query()->where('env', $env)->lockForUpdate()->firstOrFail();

            if ($setting->version !== $version) {
                throw new StaleSettingsVersion($setting->version, $version);
            }

            $before = $this->values($setting);
            $after = $this->apply($before, SettingsSchema::normalise($changes));
            $diff = $this->diff($before, $after);

            if ($diff === []) {
                return [$setting, []];
            }

            $setting->version = $setting->version + 1;
            $this->fill($setting, $after);
            $setting->save();

            $this->writeHistory($setting, $diff, $user);
            $this->audit->record(
                'settings.updated',
                'settings',
                $setting->id,
                ['env' => $env, 'version' => $version, 'data' => $before],
                ['env' => $env, 'version' => $setting->version, 'data' => $after],
                $user,
            );

            return [$setting, $diff];
        });

        [$setting, $diff] = $result;

        if ($diff !== []) {
            $this->publishChanged($setting, array_keys($diff), $user);
        }

        return $setting;
    }

    /**
     * Profile rules (brief §14.3): choosing a preset replaces the ten risk fields with it;
     * editing any of them away from the preset makes the profile "custom".
     *
     * @param  array<string, bool|int|string|list<string>|null>  $current
     * @param  array<string, bool|int|string|list<string>|null>  $changes
     * @return array<string, bool|int|string|list<string>|null>
     */
    public function apply(array $current, array $changes): array
    {
        $next = $current;
        $profile = $changes['risk_profile'] ?? $current['risk_profile'];
        $profile = is_string($profile) ? $profile : 'custom';

        if (isset($changes['risk_profile']) && in_array($profile, SettingsSchema::PRESETS, true)) {
            $next = [...$next, ...SettingsSchema::preset($profile)];
        }

        foreach ($changes as $field => $value) {
            if ($field !== 'risk_profile') {
                $next[$field] = $value;
            }
        }

        if (in_array($profile, SettingsSchema::PRESETS, true)) {
            foreach (SettingsSchema::preset($profile) as $field => $value) {
                if (! SettingsSchema::same($field, $next[$field] ?? null, $value)) {
                    $profile = 'custom';
                    break;
                }
            }
        }
        $next['risk_profile'] = $profile;

        return $next;
    }

    /**
     * @param  array<string, bool|int|string|list<string>|null>  $before
     * @param  array<string, bool|int|string|list<string>|null>  $after
     * @return array<string, array{from: bool|int|string|list<string>|null, to: bool|int|string|list<string>|null}>
     */
    private function diff(array $before, array $after): array
    {
        $diff = [];
        foreach ($after as $field => $value) {
            $old = $before[$field] ?? null;
            $same = $field === 'risk_profile' ? $old === $value : SettingsSchema::same($field, $old, $value);
            if (! $same) {
                $diff[$field] = ['from' => $old, 'to' => $value];
            }
        }

        return $diff;
    }

    /**
     * @param  array<string, bool|int|string|list<string>|null>  $data
     */
    private function fill(Setting $setting, array $data): void
    {
        $setting->data = $data;
        $setting->risk_profile = self::text($data['risk_profile']);
        $setting->autonomy_level = (int) self::text($data['autonomy_level']);
        $setting->max_loss_per_trade_pct = self::text($data['max_loss_per_trade_pct']);
        $setting->max_position_pct = self::text($data['max_position_pct']);
        $setting->max_leverage = (int) self::text($data['max_leverage']);
    }

    /**
     * A typed-column mirror of a scalar field (the mirrored fields are never bool or list).
     *
     * @param  bool|int|string|list<string>|null  $value
     */
    private static function text(bool|int|string|array|null $value): string
    {
        return is_int($value) || is_string($value) ? (string) $value : '';
    }

    /**
     * @param  array<string, array{from: bool|int|string|list<string>|null, to: bool|int|string|list<string>|null}>  $diff
     */
    private function writeHistory(Setting $setting, array $diff, ?User $user): void
    {
        SettingsHistory::query()->create([
            'settings_id' => $setting->id,
            'version' => $setting->version,
            'data' => $setting->data,
            'diff' => $diff,
            'changed_by' => $user?->id,
        ]);
    }

    /**
     * Postgres is the source of truth and Python services re-check the version before every
     * risk decision, so a Redis outage must not undo or fail a saved change: it is reported.
     *
     * @param  list<string>  $changedFields
     */
    private function publishChanged(Setting $setting, array $changedFields, ?User $user): void
    {
        try {
            $this->events->publish(
                self::EVENT_STREAM,
                self::EVENT_TYPE,
                $setting->env,
                self::EVENT_SOURCE,
                [
                    'settings_id' => $setting->id,
                    'version' => $setting->version,
                    'changed_fields' => $changedFields,
                    'changed_by' => $user?->id,
                ],
            );
        } catch (Throwable $e) {
            report($e);
        }
    }
}
