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
        // 0 until the owner finishes the onboarding wizard, then 1 (ATL-19). Finishing always
        // writes it, so the version moves past 1 even when the owner keeps every default.
        'onboarding_completed' => ['integer', 0, 1, false],
        // ATL-124, brief §14.4: money limits in USDT (decimal strings) and a daily trade count.
        'bot_capital_cap_usdt' => ['decimal', '0', '1000000000000', true],
        'max_usdt_per_trade' => ['decimal', '0', '1000000000000', true],
        'max_new_exposure_per_day_usdt' => ['decimal', '0', '1000000000000', true],
        'max_trades_per_day' => ['integer', 1, 1000, false],
        // ATL-124, brief §10: Risk Engine parameters.
        'min_stop_atr_mult' => ['decimal', '0', '20', true],
        'max_stop_pct' => ['decimal', '0', '100', true],
        'slippage_bps' => ['decimal', '0', '1000', false],
        'liq_buffer_mult' => ['decimal', '1', '20', false],
        'block_new_trades_minutes_before_high_impact_event' => ['integer', 0, 1440, false],
        'volatility_halt_sigma' => ['decimal', '0', '100', true],
        'market_data_max_age_seconds' => ['integer', 1, 3600, false],
        'margin_buffer' => ['decimal', '0', '1', true],
        // ATL-141, brief §4.4, §11: Executor parameters.
        'entry_timeout_minutes' => ['integer', 1, 1440, false],
        'stop_verify_seconds' => ['integer', 1, 60, false],
        'order_max_retries' => ['integer', 1, 10, false],
        'emergency_stop_atr_mult' => ['decimal', '0', '20', true],
    ];

    /**
     * ATL-124, decision #250: NUMERIC_FIELDS the owner may clear with JSON null. A null bot
     * capital cap means "no cap": sizing equity is the account equity.
     */
    public const NULLABLE_FIELDS = ['bot_capital_cap_usdt'];

    /** ATL-124, brief §14.5: on/off settings. */
    public const BOOLEAN_FIELDS = ['avoid_stock_perps_outside_us_hours'];

    /**
     * ATL-141: settings that take one value from a fixed list. Field => config key of the list.
     * td_mode_perps is the OKX tdMode the Executor uses for perps (brief §11).
     *
     * @var array<string, string>
     */
    public const CHOICE_FIELDS = ['td_mode_perps' => 'atlas_defaults.executor.td_modes_perps'];

    /** ATL-124: the asset-class toggles that are on, a subset of markets.kinds. */
    public const MARKET_LIST_FIELDS = ['enabled_markets'];

    /** ATL-124: instId lists, stored upper-case and unique. The blocklist always wins. */
    public const INSTRUMENT_LIST_FIELDS = ['whitelist', 'blocklist'];

    /** An OKX instId: upper-case letters/digits in dash-separated segments (BTC-USDT-SWAP). */
    public const INST_ID_PATTERN = '/^[A-Z0-9]+(-[A-Z0-9]+)*$/';

    /** ATL-113: the model tiers an agent can run on (agents.models in config/defaults.yaml). */
    public const AGENT_MODELS = ['strong', 'standard', 'routine'];

    /** ATL-113: the per-agent settings, `agents.<slug>.<setting>`, and their kind. */
    public const AGENT_SETTINGS = [
        'enabled' => 'boolean',
        'model' => 'model',
        'daily_tokens' => 'integer',
        'daily_turns' => 'integer',
    ];

    /** ATL-113: hostname lists for the web agents' fetch policy (ATL-100). */
    public const WEB_DOMAIN_FIELDS = ['agents.web.allow_domains', 'agents.web.deny_domains'];

    /**
     * Every field the settings data holds, in display order.
     *
     * @return list<string>
     */
    public static function fields(): array
    {
        return [
            'risk_profile',
            ...array_keys(self::NUMERIC_FIELDS),
            ...self::BOOLEAN_FIELDS,
            ...array_keys(self::CHOICE_FIELDS),
            ...self::MARKET_LIST_FIELDS,
            ...self::INSTRUMENT_LIST_FIELDS,
            ...array_keys(self::agentFields()),
            ...self::WEB_DOMAIN_FIELDS,
        ];
    }

    /**
     * The nine roster agents' slugs (agents.roster in config/defaults.yaml), in roster order.
     *
     * @return list<string>
     */
    public static function agentSlugs(): array
    {
        $roster = config('atlas_defaults.agents.roster');

        return is_array($roster) ? array_map(strval(...), array_keys($roster)) : [];
    }

    /**
     * `agents.<slug>.<setting>` => kind (boolean | model | integer), for every roster agent.
     *
     * @return array<string, string>
     */
    public static function agentFields(): array
    {
        $fields = [];
        foreach (self::agentSlugs() as $slug) {
            foreach (self::AGENT_SETTINGS as $setting => $kind) {
                $fields["agents.{$slug}.{$setting}"] = $kind;
            }
        }

        return $fields;
    }

    public static function agentField(string $slug, string $setting): string
    {
        return "agents.{$slug}.{$setting}";
    }

    /**
     * [min, max] of an integer agent setting (agents.settings_bounds).
     *
     * @return array{0: int, 1: int}
     */
    public static function agentIntegerBounds(string $setting): array
    {
        $min = config("atlas_defaults.agents.settings_bounds.{$setting}.min");
        $max = config("atlas_defaults.agents.settings_bounds.{$setting}.max");

        return [is_int($min) ? $min : 1, is_int($max) ? $max : PHP_INT_MAX];
    }

    /**
     * Every asset-class toggle (markets.kinds).
     *
     * @return list<string>
     */
    public static function marketKinds(): array
    {
        $kinds = config('atlas_defaults.markets.kinds');

        return is_array($kinds) ? array_values(array_map(strval(...), $kinds)) : [];
    }

    /**
     * The values a CHOICE_FIELDS setting accepts.
     *
     * @return list<string>
     */
    public static function choices(string $field): array
    {
        $list = isset(self::CHOICE_FIELDS[$field]) ? config(self::CHOICE_FIELDS[$field]) : null;

        return is_array($list) ? array_values(array_map(strval(...), $list)) : [];
    }

    public static function marketListMax(): int
    {
        $max = config('atlas_defaults.markets.list_max');

        return is_int($max) ? $max : 500;
    }

    /** An instId in its stored form (trimmed, upper-case), or null when it is not one. */
    public static function normaliseInstId(mixed $value): ?string
    {
        if (! is_string($value)) {
            return null;
        }
        $id = strtoupper(trim($value));

        return $id !== '' && strlen($id) <= 64 && preg_match(self::INST_ID_PATTERN, $id) === 1 ? $id : null;
    }

    public static function webDomainsMax(): int
    {
        $max = config('atlas_defaults.agents.settings_bounds.web_domains_max');

        return is_int($max) ? $max : 200;
    }

    /**
     * The agent settings a fresh env starts with: agents.defaults overridden by each roster entry,
     * and agents.web's lists.
     *
     * @return array<string, bool|int|string|list<string>|null>
     */
    public static function agentDefaults(): array
    {
        $base = config('atlas_defaults.agents.defaults');
        $base = is_array($base) ? $base : [];
        $roster = config('atlas_defaults.agents.roster');
        $roster = is_array($roster) ? $roster : [];

        $values = [];
        foreach (self::agentSlugs() as $slug) {
            $entry = $roster[$slug] ?? [];
            $merged = [...$base, ...(is_array($entry) ? $entry : [])];
            foreach (array_keys(self::AGENT_SETTINGS) as $setting) {
                $values[self::agentField($slug, $setting)] = $merged[$setting] ?? null;
            }
        }
        foreach (self::WEB_DOMAIN_FIELDS as $field) {
            $values[$field] = config('atlas_defaults.'.$field);
        }

        return self::normalise($values);
    }

    /**
     * A hostname in its stored form: trimmed, one trailing dot dropped, lower-case and IDNA
     * (punycode) encoded. Null when it is not a bare hostname (a URL, a port, a path, a space...).
     */
    public static function normaliseHostname(mixed $value): ?string
    {
        if (! is_string($value)) {
            return null;
        }
        $host = trim($value);
        if (str_ends_with($host, '.')) {
            $host = substr($host, 0, -1);
        }
        if ($host === '' || strlen($host) > 253 || preg_match('/[\s\/:@?#\[\]\\%*]/u', $host) === 1) {
            return null;
        }
        $ascii = idn_to_ascii(mb_strtolower($host, 'UTF-8'), IDNA_NONTRANSITIONAL_TO_ASCII, INTL_IDNA_VARIANT_UTS46);
        if (! is_string($ascii)) {
            return null;
        }
        $ascii = strtolower($ascii);
        $label = '[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?';

        return strlen($ascii) <= 253 && preg_match("/^{$label}(?:\.{$label})*$/", $ascii) === 1 ? $ascii : null;
    }

    public static function isDecimal(string $field): bool
    {
        return (self::NUMERIC_FIELDS[$field][0] ?? null) === 'decimal';
    }

    /**
     * The settings a fresh env starts with: the default risk profile, the default autonomy level
     * and the market defaults.
     *
     * @return array<string, bool|int|string|list<string>|null>
     */
    public static function defaults(): array
    {
        $profile = self::configString('atlas_defaults.risk_profiles.default');

        return [...self::normalise([
            'risk_profile' => $profile,
            ...self::preset($profile),
            'autonomy_level' => config('atlas_defaults.autonomy.default_level'),
            'max_instruments_per_day' => config('atlas_defaults.markets.max_instruments_per_day'),
            'max_spread_bps' => config('atlas_defaults.markets.max_spread_bps'),
            'min_24h_volume_usd_crypto_perp' => config('atlas_defaults.markets.min_24h_volume_usd.crypto_perp'),
            'min_24h_volume_usd_stock_perp' => config('atlas_defaults.markets.min_24h_volume_usd.stock_perp'),
            'min_24h_volume_usd_commodity_perp' => config('atlas_defaults.markets.min_24h_volume_usd.commodity_perp'),
            'min_24h_volume_usd_spot' => config('atlas_defaults.markets.min_24h_volume_usd.spot'),
            'onboarding_completed' => 0,
            'bot_capital_cap_usdt' => config('atlas_defaults.money_limits.bot_capital_cap_usdt'),
            'max_usdt_per_trade' => config('atlas_defaults.money_limits.max_usdt_per_trade'),
            'max_new_exposure_per_day_usdt' => config('atlas_defaults.money_limits.max_new_exposure_per_day_usdt'),
            'max_trades_per_day' => config('atlas_defaults.money_limits.max_trades_per_day'),
            'min_stop_atr_mult' => config('atlas_defaults.risk_engine.min_stop_atr_mult'),
            'max_stop_pct' => config('atlas_defaults.risk_engine.max_stop_pct'),
            'slippage_bps' => config('atlas_defaults.risk_engine.slippage_bps'),
            'liq_buffer_mult' => config('atlas_defaults.risk_engine.liq_buffer_mult'),
            'block_new_trades_minutes_before_high_impact_event' => config('atlas_defaults.risk_engine.block_new_trades_minutes_before_high_impact_event'),
            'volatility_halt_sigma' => config('atlas_defaults.risk_engine.volatility_halt_sigma'),
            'market_data_max_age_seconds' => config('atlas_defaults.risk_engine.market_data_max_age_seconds'),
            'margin_buffer' => config('atlas_defaults.risk_engine.margin_buffer'),
            'avoid_stock_perps_outside_us_hours' => config('atlas_defaults.markets.avoid_stock_perps_outside_us_hours'),
            'enabled_markets' => config('atlas_defaults.markets.enabled_markets'),
            'whitelist' => config('atlas_defaults.markets.whitelist'),
            'blocklist' => config('atlas_defaults.markets.blocklist'),
            'entry_timeout_minutes' => config('atlas_defaults.executor.entry_timeout_minutes'),
            'td_mode_perps' => config('atlas_defaults.executor.td_mode_perps'),
            'stop_verify_seconds' => config('atlas_defaults.executor.stop_verify_seconds'),
            'order_max_retries' => config('atlas_defaults.executor.order_max_retries'),
            'emergency_stop_atr_mult' => config('atlas_defaults.executor.emergency_stop_atr_mult'),
        ]), ...self::agentDefaults()];
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
            $values[$field] = self::numeric($field, config("atlas_defaults.risk_profiles.{$profile}.{$field}"));
        }

        return $values;
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
     * Brings values to their canonical form: decimals as plain decimal strings, integers as int,
     * agent switches and boolean fields as bool, models as their tier, domain lists as unique
     * stored hostnames, instId lists as unique upper-case instIds, enabled_markets in markets.kinds
     * order (invalid entries are dropped; validation refuses them first).
     * Unknown keys are dropped.
     *
     * @param  array<string, mixed>  $values
     * @return array<string, bool|int|string|list<string>|null>
     */
    public static function normalise(array $values): array
    {
        $agentFields = self::agentFields();
        $out = [];
        foreach ($values as $field => $value) {
            if ($field === 'risk_profile') {
                $out[$field] = is_string($value) ? $value : '';

                continue;
            }
            if (in_array($field, self::WEB_DOMAIN_FIELDS, true)) {
                $hosts = [];
                foreach (is_array($value) ? $value : [] as $entry) {
                    $host = self::normaliseHostname($entry);
                    if ($host !== null && ! in_array($host, $hosts, true)) {
                        $hosts[] = $host;
                    }
                }
                $out[$field] = $hosts;

                continue;
            }
            if (in_array($field, self::BOOLEAN_FIELDS, true)) {
                $out[$field] = $value === true;

                continue;
            }
            if (isset(self::CHOICE_FIELDS[$field])) {
                $out[$field] = is_string($value) ? $value : '';

                continue;
            }
            if (in_array($field, self::MARKET_LIST_FIELDS, true)) {
                $kinds = self::marketKinds();
                $on = is_array($value) ? $value : [];
                // Stored in markets.kinds order, so the same set always compares equal.
                $out[$field] = array_values(array_filter($kinds, fn (string $kind): bool => in_array($kind, $on, true)));

                continue;
            }
            if (in_array($field, self::INSTRUMENT_LIST_FIELDS, true)) {
                $ids = [];
                foreach (is_array($value) ? $value : [] as $entry) {
                    $id = self::normaliseInstId($entry);
                    if ($id !== null && ! in_array($id, $ids, true)) {
                        $ids[] = $id;
                    }
                }
                $out[$field] = $ids;

                continue;
            }
            if (isset($agentFields[$field])) {
                $out[$field] = match ($agentFields[$field]) {
                    'boolean' => $value === true,
                    'model' => is_string($value) ? $value : '',
                    default => is_numeric($value) ? (int) $value : 0,
                };

                continue;
            }
            if (! isset(self::NUMERIC_FIELDS[$field])) {
                continue;
            }
            $out[$field] = $value === null && in_array($field, self::NULLABLE_FIELDS, true)
                ? null
                : self::numeric($field, $value);
        }

        return $out;
    }

    /** A NUMERIC_FIELDS value in canonical form: decimals as plain decimal strings, integers as int. */
    private static function numeric(string $field, mixed $value): int|string
    {
        return self::isDecimal($field)
            ? (string) BigDecimal::of(is_int($value) ? $value : (is_string($value) ? $value : '0'))
            : (is_numeric($value) ? (int) $value : 0);
    }

    /**
     * Whether two values of a field are the same ("1" equals "1.0" for decimals; lists compare
     * in order; everything else strictly).
     *
     * @param  bool|int|string|list<string>|null  $a
     * @param  bool|int|string|list<string>|null  $b
     */
    public static function same(string $field, bool|int|string|array|null $a, bool|int|string|array|null $b): bool
    {
        if ($a === null || $b === null || is_array($a) || is_array($b) || is_bool($a) || is_bool($b)) {
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
