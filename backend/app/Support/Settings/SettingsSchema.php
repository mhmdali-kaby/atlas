<?php

declare(strict_types=1);

namespace App\Support\Settings;

use Brick\Math\BigDecimal;

/**
 * Every settings field, its type and its allowed range (brief §14). Defaults come from
 * config('atlas_defaults.*'), generated from config/defaults.yaml (ATL-1).
 *
 * Decimal fields travel and are stored as strings; integer fields as JSON integers.
 */
final class SettingsSchema
{
    public const PRESETS = ['conservative', 'balanced', 'aggressive'];

    public const PROFILES = ['conservative', 'balanced', 'aggressive', 'custom'];

    /** The ten §14.3 fields a risk profile preset sets. */
    public const RISK_FIELDS = [
        'max_loss_per_trade_pct',
        'max_position_pct',
        'max_daily_loss_pct',
        'max_weekly_loss_pct',
        'max_leverage',
        'max_open_positions',
        'max_share_per_instrument_pct',
        'max_correlated_exposure_pct',
        'min_expected_r',
        'max_consecutive_losses',
    ];

    /**
     * Field => [type, min, max, min_exclusive]. Decimal bounds are strings.
     *
     * @var array<string, array{0: 'decimal'|'integer', 1: int|string, 2: int|string, 3: bool}>
     */
    public const NUMERIC_FIELDS = [
        'max_loss_per_trade_pct' => ['decimal', '0', '100', true],
        'max_position_pct' => ['decimal', '0', '100', true],
        'max_daily_loss_pct' => ['decimal', '0', '100', true],
        'max_weekly_loss_pct' => ['decimal', '0', '100', true],
        'max_leverage' => ['integer', 1, 125, false],
        'max_open_positions' => ['integer', 1, 100, false],
        'max_share_per_instrument_pct' => ['decimal', '0', '100', true],
        'max_correlated_exposure_pct' => ['decimal', '0', '100', true],
        'min_expected_r' => ['decimal', '0', '20', true],
        'max_consecutive_losses' => ['integer', 1, 50, false],
        'autonomy_level' => ['integer', 1, 4, false],
        'max_instruments_per_day' => ['integer', 1, 10, false],
        'max_spread_bps' => ['decimal', '0', '10000', true],
        'min_24h_volume_usd_crypto_perp' => ['decimal', '0', '1000000000000', false],
        'min_24h_volume_usd_stock_perp' => ['decimal', '0', '1000000000000', false],
        'min_24h_volume_usd_commodity_perp' => ['decimal', '0', '1000000000000', false],
        'min_24h_volume_usd_spot' => ['decimal', '0', '1000000000000', false],
    ];

    /**
     * Every field the settings data holds, in display order.
     *
     * @return list<string>
     */
    public static function fields(): array
    {
        return ['risk_profile', ...array_keys(self::NUMERIC_FIELDS)];
    }

    public static function isDecimal(string $field): bool
    {
        return (self::NUMERIC_FIELDS[$field][0] ?? null) === 'decimal';
    }

    /**
     * The settings a fresh env starts with: the default risk profile, the default autonomy level
     * and the market defaults.
     *
     * @return array<string, int|string>
     */
    public static function defaults(): array
    {
        $profile = self::configString('atlas_defaults.risk_profiles.default');

        return self::normalise([
            'risk_profile' => $profile,
            ...self::preset($profile),
            'autonomy_level' => config('atlas_defaults.autonomy.default_level'),
            'max_instruments_per_day' => config('atlas_defaults.markets.max_instruments_per_day'),
            'max_spread_bps' => config('atlas_defaults.markets.max_spread_bps'),
            'min_24h_volume_usd_crypto_perp' => config('atlas_defaults.markets.min_24h_volume_usd.crypto_perp'),
            'min_24h_volume_usd_stock_perp' => config('atlas_defaults.markets.min_24h_volume_usd.stock_perp'),
            'min_24h_volume_usd_commodity_perp' => config('atlas_defaults.markets.min_24h_volume_usd.commodity_perp'),
            'min_24h_volume_usd_spot' => config('atlas_defaults.markets.min_24h_volume_usd.spot'),
        ]);
    }

    /**
     * The ten §14.3 values of one preset.
     *
     * @return array<string, int|string>
     */
    public static function preset(string $profile): array
    {
        if (! in_array($profile, self::PRESETS, true)) {
            throw new \InvalidArgumentException("Unknown risk profile preset [{$profile}].");
        }

        $values = [];
        foreach (self::RISK_FIELDS as $field) {
            $values[$field] = config("atlas_defaults.risk_profiles.{$profile}.{$field}");
        }

        return self::normalise($values);
    }

    /**
     * @return array<string, array<string, int|string>>
     */
    public static function presets(): array
    {
        $presets = [];
        foreach (self::PRESETS as $profile) {
            $presets[$profile] = self::preset($profile);
        }

        return $presets;
    }

    /**
     * Brings values to their canonical form: decimals as plain decimal strings, integers as int.
     * Unknown keys are dropped.
     *
     * @param  array<string, mixed>  $values
     * @return array<string, int|string>
     */
    public static function normalise(array $values): array
    {
        $out = [];
        foreach ($values as $field => $value) {
            if ($field === 'risk_profile') {
                $out[$field] = is_string($value) ? $value : '';

                continue;
            }
            if (! isset(self::NUMERIC_FIELDS[$field])) {
                continue;
            }
            $out[$field] = self::isDecimal($field)
                ? (string) BigDecimal::of(is_int($value) ? $value : (is_string($value) ? $value : '0'))
                : (is_numeric($value) ? (int) $value : 0);
        }

        return $out;
    }

    /**
     * Whether two values of a field are the same number ("1" equals "1.0").
     */
    public static function same(string $field, int|string|null $a, int|string|null $b): bool
    {
        if ($a === null || $b === null) {
            return $a === $b;
        }
        if (self::isDecimal($field)) {
            return BigDecimal::of($a)->isEqualTo($b);
        }

        return (string) $a === (string) $b;
    }

    private static function configString(string $key): string
    {
        $value = config($key);

        return is_string($value) ? $value : '';
    }
}
