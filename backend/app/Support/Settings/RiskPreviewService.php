<?php

declare(strict_types=1);

namespace App\Support\Settings;

use App\Models\User;
use Brick\Math\BigDecimal;
use Brick\Math\RoundingMode;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;

/**
 * GET /api/settings/risk-preview (brief §14.4): "With equity $X, your next trade can risk at
 * most $Y and use at most $Z." Reads the latest synced equity and the current (or drafted)
 * settings; never writes anything.
 *
 * Decision #250 folds the bot capital cap into the sizing equity every percentage limit is
 * computed on: sizing_equity_usd = min(equity, cap), or equity when the cap is null. max_risk_usd
 * has one term (max_loss_per_trade_pct of the sizing equity); max_notional_usd is the smallest of
 * three (max_position_pct of the sizing equity, max_usdt_per_trade, and what is left of
 * max_new_exposure_per_day_usdt after today's atlas entry orders), each reported with the name of
 * the limit that bound it so the Settings page can say why the next trade is capped.
 */
final class RiskPreviewService
{
    public const MONEY_SCALE = 2;

    public const REASON_NO_EQUITY = 'no_equity';

    /** Decimal scale carried through intermediate division before the final 2-dp rounding. */
    private const WORKING_SCALE = 10;

    /** Cap names, in the order ties are broken (first minimal wins) — matches ATL-126's sizing caps. */
    private const NOTIONAL_CAPS_ORDER = ['max_position_pct', 'max_usdt_per_trade', 'max_new_exposure_per_day'];

    public const OWNER_TIMEZONE = 'Asia/Baghdad';

    public function __construct(private readonly SettingsService $settings) {}

    /**
     * @param  array<string, string|null>  $drafts  RiskPreviewRequest::drafts(), already validated
     * @return array<string, mixed>
     */
    public function preview(string $env, array $drafts, ?User $user = null): array
    {
        $setting = $this->settings->current($env, $user);
        $values = $this->settings->apply($this->settings->values($setting), $drafts);

        $equityRow = DB::table('equity_curve')->where('env', $env)->orderByDesc('ts')->first(['ts', 'total_eq_usd']);
        $equity = $equityRow === null ? null : BigDecimal::of((string) $equityRow->total_eq_usd);

        [$usedExposure, $tradesToday] = $this->todaysUsage($env);

        if ($equity === null) {
            return [
                'env' => $env,
                'settings_version' => $setting->version,
                'equity_usd' => null,
                'equity_as_of' => null,
                'sizing_equity_usd' => null,
                'cap_binds' => false,
                'max_risk_usd' => null,
                'risk_binding_limit' => null,
                'max_notional_usd' => null,
                'notional_binding_limit' => null,
                'used_exposure_today_usd' => $this->money($usedExposure),
                'trades_today' => $tradesToday,
                'reason' => self::REASON_NO_EQUITY,
            ];
        }

        $capRaw = $values['bot_capital_cap_usdt'] ?? null;
        $cap = is_string($capRaw) ? BigDecimal::of($capRaw) : null;
        $capBinds = $cap !== null && $cap->isLessThan($equity);
        $sizingEquity = $capBinds ? $cap : $equity;

        $lossPct = $this->decimalField($values, 'max_loss_per_trade_pct');
        $maxRisk = $sizingEquity->multipliedBy($lossPct)->dividedBy(100, self::WORKING_SCALE, RoundingMode::HalfUp);

        $positionPct = $this->decimalField($values, 'max_position_pct');
        $byPosition = $sizingEquity->multipliedBy($positionPct)->dividedBy(100, self::WORKING_SCALE, RoundingMode::HalfUp);
        $maxUsdtPerTrade = $this->decimalField($values, 'max_usdt_per_trade');
        $dailyAllowance = $this->decimalField($values, 'max_new_exposure_per_day_usdt');
        $remaining = $dailyAllowance->minus($usedExposure);
        if ($remaining->isNegative()) {
            $remaining = BigDecimal::zero();
        }

        [$maxNotional, $notionalBindingLimit] = $this->smallest([
            'max_position_pct' => $byPosition,
            'max_usdt_per_trade' => $maxUsdtPerTrade,
            'max_new_exposure_per_day' => $remaining,
        ]);

        return [
            'env' => $env,
            'settings_version' => $setting->version,
            'equity_usd' => $this->money($equity),
            'equity_as_of' => $this->iso($equityRow->ts),
            'sizing_equity_usd' => $this->money($sizingEquity),
            'cap_binds' => $capBinds,
            'max_risk_usd' => $this->money($maxRisk),
            'risk_binding_limit' => 'max_loss_per_trade_pct',
            'max_notional_usd' => $this->money($maxNotional),
            'notional_binding_limit' => $notionalBindingLimit,
            'used_exposure_today_usd' => $this->money($usedExposure),
            'trades_today' => $tradesToday,
            'reason' => null,
        ];
    }

    /**
     * @param  array<string, BigDecimal>  $terms
     * @return array{0: BigDecimal, 1: string}
     */
    private function smallest(array $terms): array
    {
        $bestKey = self::NOTIONAL_CAPS_ORDER[0];
        $bestValue = $terms[$bestKey];
        foreach (self::NOTIONAL_CAPS_ORDER as $key) {
            $value = $terms[$key];
            if ($value->isLessThan($bestValue)) {
                $bestValue = $value;
                $bestKey = $key;
            }
        }

        return [$bestValue, $bestKey];
    }

    /**
     * Today's (Asia/Baghdad business day) atlas entry orders: count and their risk-checked USD
     * notional, the figure max_new_exposure_per_day_usdt caps. Zero before the Executor (patch 23)
     * ever places one.
     *
     * @return array{0: BigDecimal, 1: int}
     */
    private function todaysUsage(string $env): array
    {
        $todayStart = CarbonImmutable::now(self::OWNER_TIMEZONE)->startOfDay()->utc();

        $row = DB::table('orders as o')
            ->join('risk_checks as rc', 'rc.id', '=', 'o.risk_check_id')
            ->where('o.env', $env)
            ->where('o.origin', 'atlas')
            ->where('o.purpose', 'entry')
            ->where('o.created_at', '>=', $todayStart->format('Y-m-d H:i:s.uP'))
            ->selectRaw('count(*) AS n, coalesce(sum(rc.final_notional_usd), 0) AS notional')
            ->first();

        $notional = $row === null ? BigDecimal::zero() : BigDecimal::of((string) $row->notional);
        $n = $row === null ? 0 : (int) $row->n;

        return [$notional, $n];
    }

    /**
     * A decimal-typed settings value (never bot_capital_cap_usdt, which is nullable) as a BigDecimal.
     *
     * @param  array<string, bool|int|string|list<string>|null>  $values
     */
    private function decimalField(array $values, string $field): BigDecimal
    {
        $value = $values[$field] ?? null;

        return BigDecimal::of(is_int($value) || is_string($value) ? $value : '0');
    }

    private function money(BigDecimal $value): string
    {
        return (string) $value->toScale(self::MONEY_SCALE, RoundingMode::HalfUp);
    }

    private function iso(mixed $ts): string
    {
        return CarbonImmutable::parse(is_string($ts) ? $ts : (string) $ts)->utc()->toIso8601ZuluString('microsecond');
    }
}
