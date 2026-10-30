<?php

declare(strict_types=1);

namespace App\Support\Settings;

use App\Models\Setting;
use Brick\Math\BigDecimal;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;

/**
 * ATL-155, decision #250 (1): which Settings -> Markets filter each instrument currently fails.
 *
 * The filters are Market Scout's universe filter (atlas_agents.scout.universe.filter_universe),
 * read the same way: market toggle from inst_type + asset_class, OKX state, blocklist
 * (case-insensitive), listed more than agents.scout.universe.min_listed_days ago, 24h USD quote
 * volume from closed 1H candles against the market's minimum, spread against max_spread_bps.
 * Codes, in this order: blocked, unknown_instrument, asset_class_disabled, not_live,
 * listed_under_7_days, min_24h_volume, max_spread, no_data.
 *
 * Two places differ from the Scout on purpose, because the owner needs to see a gap rather than a
 * wrong reason: no closed 1H bar in the last 2 x 24h, or a quote currency that is not counted as
 * USD, is `no_data` (the Scout counts the first as zero volume and the second as volume_unknown).
 * An unknown spread is not a failure (the Scout keeps it with a spread_unknown note); it is
 * reported in `notes`.
 */
class MarketsEligibility
{
    public const CODES = [
        'blocked', 'unknown_instrument', 'asset_class_disabled', 'not_live',
        'listed_under_7_days', 'min_24h_volume', 'max_spread', 'no_data',
    ];

    public const VOLUME_BASIS = '24h quote volume summed from closed 1H candles (the Market Scout basis); USDT, USDC and USD quotes counted 1:1 as USD';

    public const SPREAD_BASIS = 'live OKX ticker best bid/ask from trading_core, only when pushed within market_data_max_age_seconds';

    private const STOCK_CLASSES = ['stock', 'etf', 'index'];

    public function __construct(
        private readonly SettingsService $settings,
        private readonly TickerSpreads $spreads,
    ) {}

    /**
     * @param  list<string>  $instIds  stored form (upper-case)
     * @param  array<string, bool|int|string|list<string>|null>  $draft  canonical overrides
     * @return array<string, mixed>
     */
    public function check(string $env, array $instIds, array $draft = [], ?CarbonImmutable $now = null): array
    {
        $now ??= CarbonImmutable::now('UTC');
        $row = Setting::query()->where('env', $env)->first();
        $values = $row !== null ? $this->settings->values($row) : SettingsSchema::defaults();
        $values = [...$values, ...$draft];

        $enabled = self::stringList($values['enabled_markets'] ?? []);
        $blocklist = array_map('strtoupper', self::stringList($values['blocklist'] ?? []));
        $whitelist = array_map('strtoupper', self::stringList($values['whitelist'] ?? []));
        $maxSpread = BigDecimal::of(self::decimalString($values['max_spread_bps'] ?? '0'));
        $minVolume = [];
        foreach (['spot', 'crypto_perp', 'stock_perp', 'commodity_perp'] as $market) {
            $minVolume[$market] = BigDecimal::of(self::decimalString($values["min_24h_volume_usd_{$market}"] ?? '0'));
        }
        $minListedDays = (int) config('atlas_defaults.agents.scout.universe.min_listed_days', 7);
        $usdQuotes = array_map('strtoupper', self::stringList(config('atlas_defaults.agents.scout.universe.usd_quote_ccys', ['USDT', 'USDC', 'USD'])));
        $maxAge = $values['market_data_max_age_seconds'] ?? config('atlas_defaults.market_ws.market_data_max_age_seconds', 30);

        $instruments = $this->instruments($env, $instIds, $now);
        $volumes = $this->volumes($env, array_keys($instruments), $now);
        $spreads = $this->spreads->spreadsBps($env, array_keys($instruments), $now, is_numeric($maxAge) ? (int) $maxAge : 30);
        $listedCutoff = $now->subDays($minListedDays);

        $results = [];
        foreach ($instIds as $instId) {
            $fails = [];
            $notes = [];
            if (in_array($instId, $blocklist, true)) {
                $fails[] = 'blocked';
            }
            $inst = $instruments[$instId] ?? null;
            if ($inst === null) {
                $fails[] = 'unknown_instrument';
                $results[] = self::result($instId, $fails, $notes, null, $whitelist, null, null, null, null);

                continue;
            }

            $market = self::marketOf($inst->inst_type, $inst->asset_class);
            if (! in_array($market, $enabled, true)) {
                $fails[] = 'asset_class_disabled';
            }
            if ($inst->state !== 'live') {
                $fails[] = 'not_live';
            }
            if ($inst->list_time === null) {
                $notes[] = 'age_unknown';
            } elseif (CarbonImmutable::parse($inst->list_time)->greaterThanOrEqualTo($listedCutoff)) {
                $fails[] = 'listed_under_7_days';
            }

            $noData = false;
            $volume = null;
            $minimum = $minVolume[$market] ?? null;
            if (! in_array(self::volumeCcy($inst), $usdQuotes, true)) {
                $noData = true;
                $notes[] = 'volume_not_usd';
            } elseif (! isset($volumes[$instId])) {
                $noData = true;
                $notes[] = 'volume_unknown';
            } else {
                $volume = $volumes[$instId];
                if ($minimum !== null && $volume->isLessThan($minimum)) {
                    $fails[] = 'min_24h_volume';
                }
            }

            $spread = isset($spreads[$instId]) ? BigDecimal::of($spreads[$instId]) : null;
            if ($spread === null) {
                $notes[] = 'spread_unknown';
            } elseif ($spread->isGreaterThan($maxSpread)) {
                $fails[] = 'max_spread';
            }
            if ($noData) {
                $fails[] = 'no_data';
            }

            $results[] = self::result($instId, $fails, $notes, $market, $whitelist, $volume, $minimum, $spread, $maxSpread);
        }

        return [
            'env' => $env,
            'as_of' => $now->toIso8601ZuluString('millisecond'),
            'settings_version' => $row?->version,
            'draft' => array_keys($draft),
            'volume_basis' => self::VOLUME_BASIS,
            'spread_basis' => self::SPREAD_BASIS,
            'instruments' => $results,
        ];
    }

    /**
     * @param  list<string>  $fails
     * @param  list<string>  $notes
     * @param  list<string>  $whitelist
     * @return array<string, mixed>
     */
    private static function result(
        string $instId,
        array $fails,
        array $notes,
        ?string $market,
        array $whitelist,
        ?BigDecimal $volume,
        ?BigDecimal $minVolume,
        ?BigDecimal $spread,
        ?BigDecimal $maxSpread,
    ): array {
        return [
            'inst_id' => $instId,
            'eligible' => $fails === [],
            'fails' => $fails,
            'market' => $market,
            'on_whitelist' => in_array($instId, $whitelist, true),
            'volume_24h_usd' => $volume !== null ? (string) $volume : null,
            'min_24h_volume_usd' => $minVolume !== null ? (string) $minVolume : null,
            'spread_bps' => $spread !== null ? (string) $spread : null,
            'max_spread_bps' => $maxSpread !== null ? (string) $maxSpread : null,
            'notes' => $notes,
        ];
    }

    /**
     * Instruments of the env known by $now (Scout's `listed`: coalesce(list_time, created_at) <= now).
     *
     * @param  list<string>  $instIds
     * @return array<string, object{inst_id: string, inst_type: string, asset_class: string, state: string, list_time: ?string, quote_ccy: ?string, settle_ccy: ?string}>
     */
    private function instruments(string $env, array $instIds, CarbonImmutable $now): array
    {
        if ($instIds === []) {
            return [];
        }
        $rows = DB::table('instruments')
            ->select(['inst_id', 'inst_type', 'asset_class', 'state', 'list_time', 'quote_ccy', 'settle_ccy'])
            ->where('env', $env)
            ->whereIn('inst_id', $instIds)
            ->whereRaw('coalesce(list_time, created_at) <= ?', [$now])
            ->get();
        $out = [];
        foreach ($rows as $row) {
            /** @var object{inst_id: string, inst_type: string, asset_class: string, state: string, list_time: ?string, quote_ccy: ?string, settle_ccy: ?string} $row */
            $out[$row->inst_id] = $row;
        }

        return $out;
    }

    /**
     * 24h quote volume per instrument, as atlas_agents.tools.market.compute_tickers computes it:
     * confirmed bars of market_tools.ticker_bar closed by $now within the last 2 windows, summed
     * over the window ending at each instrument's newest bar. Bar quote volume is volCcyQuote,
     * else vol_ccy x close, else 0.
     *
     * @param  list<string>  $instIds
     * @return array<string, BigDecimal>
     */
    private function volumes(string $env, array $instIds, CarbonImmutable $now): array
    {
        if ($instIds === []) {
            return [];
        }
        $bar = (string) config('atlas_defaults.agents.market_tools.ticker_bar', '1H');
        $windowHours = (int) config('atlas_defaults.agents.market_tools.ticker_window_hours', 24);
        $barSeconds = self::barSeconds($bar);

        $rows = DB::table('candles')
            ->select(['inst_id', 'ts', 'c', 'vol_ccy', 'vol_ccy_quote'])
            ->where('env', $env)
            ->where('bar', $bar)
            ->where('confirmed', true)
            ->whereIn('inst_id', $instIds)
            ->where('ts', '<=', $now->subSeconds($barSeconds))
            ->where('ts', '>', $now->subHours(2 * $windowHours))
            ->orderBy('inst_id')
            ->orderBy('ts')
            ->get();

        $byInst = [];
        foreach ($rows as $row) {
            /** @var object{inst_id: string, ts: string, c: string, vol_ccy: ?string, vol_ccy_quote: ?string} $row */
            $byInst[$row->inst_id][] = $row;
        }
        $out = [];
        foreach ($byInst as $instId => $candles) {
            $last = CarbonImmutable::parse($candles[array_key_last($candles)]->ts);
            $windowStart = $last->addSeconds($barSeconds)->subHours($windowHours);
            $sum = BigDecimal::zero();
            foreach ($candles as $candle) {
                if (CarbonImmutable::parse($candle->ts)->lessThan($windowStart)) {
                    continue;
                }
                if ($candle->vol_ccy_quote !== null) {
                    $sum = $sum->plus($candle->vol_ccy_quote);
                } elseif ($candle->vol_ccy !== null) {
                    $sum = $sum->plus(BigDecimal::of($candle->vol_ccy)->multipliedBy($candle->c));
                }
            }
            $out[(string) $instId] = $sum->strippedOfTrailingZeros();
        }

        return $out;
    }

    /** The Settings -> Markets toggle an instrument falls under (Scout's market_of). */
    public static function marketOf(string $instType, string $assetClass): string
    {
        return match (strtoupper($instType)) {
            'SPOT' => 'spot',
            'FUTURES' => 'futures',
            'OPTION' => 'options',
            'SWAP' => match (true) {
                $assetClass === 'crypto' => 'crypto_perp',
                in_array($assetClass, self::STOCK_CLASSES, true) => 'stock_perp',
                $assetClass === 'commodity' => 'commodity_perp',
                default => 'other',
            },
            default => 'other',
        };
    }

    /**
     * The currency OKX's volCcyQuote is in (Scout's _volume_ccy): quoteCcy, else the instId's
     * second segment (OKX leaves quoteCcy empty on swaps), else settleCcy.
     *
     * @param  object{inst_id: string, quote_ccy: ?string, settle_ccy: ?string}  $inst
     */
    private static function volumeCcy(object $inst): ?string
    {
        $parts = explode('-', $inst->inst_id);
        $ccy = ($inst->quote_ccy ?? '') !== '' ? $inst->quote_ccy : (($parts[1] ?? '') !== '' ? $parts[1] : $inst->settle_ccy);

        return $ccy !== null && $ccy !== '' ? strtoupper($ccy) : null;
    }

    private static function barSeconds(string $bar): int
    {
        $unit = substr($bar, -1);
        $n = (int) substr($bar, 0, -1);

        return $n * match ($unit) {
            'm' => 60,
            'H' => 3600,
            'D' => 86400,
            default => 3600,
        };
    }

    /**
     * @return list<string>
     */
    private static function stringList(mixed $value): array
    {
        return is_array($value) ? array_values(array_filter($value, is_string(...))) : [];
    }

    private static function decimalString(mixed $value): string
    {
        return is_string($value) || is_int($value) ? (string) $value : '0';
    }
}
