<?php

declare(strict_types=1);

namespace App\Support\Notifications;

use Carbon\CarbonImmutable;
use Carbon\CarbonInterface;

/**
 * Quiet hours from the env's schedule settings (ATL-229, decision #255 (2)).
 *
 * `schedule.quiet_hours_enabled` with `schedule.quiet_hours_start` / `_end` ("HH:MM", Asia/Baghdad
 * wall clock, `timezone.display`). The window is [start, end) and crosses midnight when start is
 * later than end. Inside it, browser and Telegram delivery is suppressed for every event not in
 * `notifications.quiet_hours_always_deliver`; the in-app row is still written.
 */
final class QuietHours
{
    /**
     * @param  array<string, mixed>  $values  SettingsService::values() of the env's row
     */
    public static function active(array $values, CarbonInterface $now): bool
    {
        if (($values['schedule.quiet_hours_enabled'] ?? false) !== true) {
            return false;
        }
        $start = self::minutes($values['schedule.quiet_hours_start'] ?? null);
        $end = self::minutes($values['schedule.quiet_hours_end'] ?? null);
        if ($start === null || $end === null || $start === $end) {
            return false;
        }
        $zone = config('atlas_defaults.timezone.display');
        $local = CarbonImmutable::instance($now)->setTimezone(is_string($zone) ? $zone : 'Asia/Baghdad');
        $current = $local->hour * 60 + $local->minute;

        return $start < $end
            ? $current >= $start && $current < $end
            : $current >= $start || $current < $end;
    }

    public static function alwaysDelivered(string $event): bool
    {
        $events = config('atlas_defaults.notifications.quiet_hours_always_deliver');

        return is_array($events) && in_array($event, $events, true);
    }

    private static function minutes(mixed $value): ?int
    {
        if (! is_string($value) || preg_match('/^([01]\d|2[0-3]):([0-5]\d)$/', $value, $m) !== 1) {
            return null;
        }

        return (int) $m[1] * 60 + (int) $m[2];
    }
}
