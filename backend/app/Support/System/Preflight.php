<?php

declare(strict_types=1);

namespace App\Support\System;

use App\Models\Connection;
use App\Models\Setting;
use App\Models\SystemState;
use App\Support\Settings\SettingsService;
use Brick\Math\BigDecimal;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;

/**
 * The live pre-flight checklist (brief §14.11, decision #255 (5)), computed in code. Five items are
 * read from the database; three are owner attestations stored on system_state. Reading it never
 * writes anything: a missing live settings row is reported, not created.
 *
 * Every item is {key, kind: computed|attested, ok, detail}; the detail is a translated line.
 */
final class Preflight
{
    public const COMPUTED = ['live_connection', 'demo_history', 'live_risk_profile', 'live_autonomy', 'live_capital_cap'];

    public const ATTESTED = ['ip_allowlist', 'sub_account', 'demo_reviewed'];

    public function __construct(private readonly SettingsService $settings) {}

    /** @return list<string> */
    public static function keys(): array
    {
        return [...self::COMPUTED, ...self::ATTESTED];
    }

    /**
     * @return list<array{key: string, kind: string, ok: bool, detail: string}>
     */
    public function items(?SystemState $state, CarbonImmutable $now): array
    {
        $live = Setting::query()->where('env', 'live')->first();
        $values = $live !== null ? $this->settings->values($live) : null;

        $items = [
            $this->liveConnection(),
            $this->demoHistory($now),
            $this->riskProfile($live),
            $this->autonomy($values),
            $this->capitalCap($values),
        ];

        $attestations = $state !== null ? $state->preflight_attestations : [];
        foreach (self::ATTESTED as $key) {
            $at = $attestations[$key]['confirmed_at'] ?? null;
            $items[] = is_string($at)
                ? self::item($key, 'attested', true, __('environment.preflight.attested.ok', ['at' => $at]))
                : self::item($key, 'attested', false, __("environment.preflight.{$key}.fail"));
        }

        return $items;
    }

    /**
     * @param  list<array{key: string, kind: string, ok: bool, detail: string}>  $items
     * @return list<string> the keys of every item that is not ok
     */
    public static function failing(array $items): array
    {
        return array_values(array_map(
            static fn (array $item): string => $item['key'],
            array_filter($items, static fn (array $item): bool => ! $item['ok']),
        ));
    }

    /** @return array{key: string, kind: string, ok: bool, detail: string} */
    private function liveConnection(): array
    {
        $connection = Connection::query()->where('exchange', 'okx')->where('env', 'live')->first();
        if ($connection === null) {
            return self::item('live_connection', 'computed', false, __('environment.preflight.live_connection.missing'));
        }
        // ATL-235: the pair is set only by a test where OKX listed no withdraw permission.
        $verified = $connection->getAttribute('last_verified_at') !== null;
        $withdrawOff = $connection->getAttribute('withdraw_off_verified_at') !== null;
        if (! $verified) {
            return self::item('live_connection', 'computed', false, __('environment.preflight.live_connection.unverified'));
        }
        if (! $withdrawOff) {
            return self::item('live_connection', 'computed', false, __('environment.preflight.live_connection.withdraw'));
        }

        return self::item('live_connection', 'computed', true, __('environment.preflight.live_connection.ok'));
    }

    /** @return array{key: string, kind: string, ok: bool, detail: string} */
    private function demoHistory(CarbonImmutable $now): array
    {
        $days = self::intConfig('atlas_defaults.env.live_preflight.min_demo_days', 28);
        $earliest = DB::table('equity_curve')->where('env', 'demo')->min('ts');
        if (! is_string($earliest)) {
            return self::item('demo_history', 'computed', false, __('environment.preflight.demo_history.none', ['days' => $days]));
        }
        $since = CarbonImmutable::parse($earliest);
        $have = (int) floor($since->diffInDays($now, true));
        $ok = $since->lessThanOrEqualTo($now->subDays($days));

        return self::item('demo_history', 'computed', $ok, __(
            $ok ? 'environment.preflight.demo_history.ok' : 'environment.preflight.demo_history.short',
            ['have' => $have, 'days' => $days],
        ));
    }

    /** @return array{key: string, kind: string, ok: bool, detail: string} */
    private function riskProfile(?Setting $live): array
    {
        if ($live === null || $live->risk_profile === '') {
            return self::item('live_risk_profile', 'computed', false, __('environment.preflight.live_risk_profile.fail'));
        }

        return self::item('live_risk_profile', 'computed', true, __('environment.preflight.live_risk_profile.ok', [
            'profile' => __("environment.profiles.{$live->risk_profile}"),
        ]));
    }

    /**
     * @param  array<string, mixed>|null  $values
     * @return array{key: string, kind: string, ok: bool, detail: string}
     */
    private function autonomy(?array $values): array
    {
        $max = self::intConfig('atlas_defaults.env.live_preflight.max_live_autonomy', 2);
        $level = $values !== null && is_int($values['autonomy_level'] ?? null) ? $values['autonomy_level'] : null;
        if ($level === null) {
            return self::item('live_autonomy', 'computed', false, __('environment.preflight.live_autonomy.none', ['max' => $max]));
        }
        $ok = $level <= $max;

        return self::item('live_autonomy', 'computed', $ok, __(
            $ok ? 'environment.preflight.live_autonomy.ok' : 'environment.preflight.live_autonomy.fail',
            ['level' => $level, 'max' => $max],
        ));
    }

    /**
     * @param  array<string, mixed>|null  $values
     * @return array{key: string, kind: string, ok: bool, detail: string}
     */
    private function capitalCap(?array $values): array
    {
        $cap = $values['bot_capital_cap_usdt'] ?? null;
        if (! is_string($cap) && ! is_int($cap)) {
            return self::item('live_capital_cap', 'computed', false, __('environment.preflight.live_capital_cap.fail'));
        }
        $cap = (string) $cap;
        if (! is_numeric($cap) || ! BigDecimal::of($cap)->isPositive()) {
            return self::item('live_capital_cap', 'computed', false, __('environment.preflight.live_capital_cap.fail'));
        }

        return self::item('live_capital_cap', 'computed', true, __('environment.preflight.live_capital_cap.ok', ['cap' => $cap]));
    }

    /** @return array{key: string, kind: string, ok: bool, detail: string} */
    private static function item(string $key, string $kind, bool $ok, mixed $detail): array
    {
        return ['key' => $key, 'kind' => $kind, 'ok' => $ok, 'detail' => is_string($detail) ? $detail : ''];
    }

    private static function intConfig(string $key, int $fallback): int
    {
        $value = config($key);

        return is_int($value) ? $value : $fallback;
    }
}
