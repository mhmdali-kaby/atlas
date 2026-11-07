<?php

declare(strict_types=1);

use App\Models\Setting;
use App\Models\User;
use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

/** ATL-129: GET /api/settings/risk-preview (brief §14.4, decision #250). */
beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
});

function riskPreviewTs(CarbonImmutable $t): string
{
    return $t->utc()->format('Y-m-d H:i:s.uP');
}

function seedRiskPreviewEquity(string $env, string $equity, ?CarbonImmutable $at = null): void
{
    $at ??= CarbonImmutable::now();
    DB::table('equity_curve')->insert([
        'id' => (string) Str::uuid(), 'env' => $env, 'ts' => riskPreviewTs($at), 'total_eq_usd' => $equity,
        'created_at' => riskPreviewTs($at), 'updated_at' => riskPreviewTs($at),
    ]);
}

/** An atlas entry order with a risk check behind it, counted by "today's used exposure". */
function seedRiskPreviewEntryOrder(string $env, string $finalNotionalUsd, CarbonImmutable $createdAt): void
{
    $proposal = (string) Str::uuid();
    DB::table('proposals')->insert([
        'id' => $proposal, 'env' => $env, 'author' => 'strategist', 'inst_id' => 'BTC-USDT-SWAP',
        'side' => 'buy', 'order_type' => 'limit', 'entry' => '68000.5', 'stop' => '66900',
        'status' => 'sent',
    ]);
    $check = (string) Str::uuid();
    DB::table('risk_checks')->insert([
        'id' => $check, 'env' => $env, 'proposal_id' => $proposal, 'settings_version' => 1,
        'result' => 'approved', 'decision' => 'send_now', 'final_size' => '1',
        'final_notional_usd' => $finalNotionalUsd,
    ]);
    DB::table('orders')->insert([
        'id' => (string) Str::uuid(), 'env' => $env, 'proposal_id' => $proposal, 'risk_check_id' => $check,
        'inst_id' => 'BTC-USDT-SWAP', 'side' => 'buy', 'pos_side' => 'net', 'ord_type' => 'limit',
        'td_mode' => 'isolated', 'px' => '68000.5', 'sz' => '1', 'origin' => 'atlas', 'purpose' => 'entry',
        'cl_ord_id' => 'atl'.Str::lower(Str::random(20)),
        'created_at' => riskPreviewTs($createdAt), 'updated_at' => riskPreviewTs($createdAt),
    ]);
}

it('returns max_risk_usd, max_notional_usd and the binding notional limit (AC1)', function (): void {
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => [
        'bot_capital_cap_usdt' => null, 'max_usdt_per_trade' => '3000',
    ]])->assertOk();
    seedRiskPreviewEquity('demo', '100000');

    $this->getJson('/api/settings/risk-preview?env=demo')
        ->assertOk()
        ->assertJsonPath('data.equity_usd', '100000.00')
        ->assertJsonPath('data.sizing_equity_usd', '100000.00')
        ->assertJsonPath('data.cap_binds', false)
        ->assertJsonPath('data.max_risk_usd', '1000.00')
        ->assertJsonPath('data.risk_binding_limit', 'max_loss_per_trade_pct')
        ->assertJsonPath('data.max_notional_usd', '3000.00')
        ->assertJsonPath('data.notional_binding_limit', 'max_usdt_per_trade')
        ->assertJsonPath('data.reason', null);
});

it('previews a drafted value without writing a settings version (AC2)', function (): void {
    $this->getJson('/api/settings?env=demo')->assertOk();
    seedRiskPreviewEquity('demo', '100000');

    // Undrafted: balanced max_position_pct (5%) of the sizing equity (capped to the default
    // 10000 bot capital cap) = 500.00, smaller than max_usdt_per_trade (1000) and the daily
    // allowance (5000).
    $this->getJson('/api/settings/risk-preview?env=demo')
        ->assertOk()
        ->assertJsonPath('data.max_notional_usd', '500.00')
        ->assertJsonPath('data.notional_binding_limit', 'max_position_pct');

    $this->getJson('/api/settings/risk-preview?env=demo&max_position_pct=2')
        ->assertOk()
        ->assertJsonPath('data.max_notional_usd', '200.00')
        ->assertJsonPath('data.notional_binding_limit', 'max_position_pct');

    expect(Setting::query()->where('env', 'demo')->value('version'))->toBe(1);
});

it('returns 200 with equity_usd null and reason no_equity when nothing has synced, and 422 on a bad draft (AC3)', function (): void {
    $this->getJson('/api/settings/risk-preview?env=demo')
        ->assertOk()
        ->assertJsonPath('data.equity_usd', null)
        ->assertJsonPath('data.reason', 'no_equity')
        ->assertJsonPath('data.max_risk_usd', null)
        ->assertJsonPath('data.max_notional_usd', null);

    $this->getJson('/api/settings/risk-preview?env=demo&max_position_pct=not-a-number')
        ->assertUnprocessable()
        ->assertJsonValidationErrors('max_position_pct');

    $this->getJson('/api/settings/risk-preview?env=demo&max_position_pct=0')
        ->assertUnprocessable()
        ->assertJsonValidationErrors('max_position_pct');
});

it('never puts a float in the response and every money field has exactly two decimals (AC4)', function (): void {
    seedRiskPreviewEquity('demo', '12345.678');

    $response = $this->getJson('/api/settings/risk-preview?env=demo')->assertOk();
    $data = $response->json('data');

    foreach (['equity_usd', 'sizing_equity_usd', 'max_risk_usd', 'max_notional_usd', 'used_exposure_today_usd'] as $field) {
        expect($data[$field])->toBeString()
            ->and($data[$field])->toMatch('/^\d+\.\d{2}$/');
    }

    $walk = function (mixed $value) use (&$walk): void {
        if (is_array($value)) {
            foreach ($value as $v) {
                $walk($v);
            }

            return;
        }
        expect(is_float($value))->toBeFalse();
    };
    $walk($response->json());
});

it('folds the bot capital cap into the sizing equity and reports it separately (decision #250)', function (): void {
    seedRiskPreviewEquity('demo', '5000000');

    // Default settings: balanced profile, bot_capital_cap_usdt 10000 — the default from ATL-124.
    $this->getJson('/api/settings/risk-preview?env=demo')
        ->assertOk()
        ->assertJsonPath('data.equity_usd', '5000000.00')
        ->assertJsonPath('data.sizing_equity_usd', '10000.00')
        ->assertJsonPath('data.cap_binds', true)
        ->assertJsonPath('data.max_risk_usd', '100.00')
        ->assertJsonPath('data.risk_binding_limit', 'max_loss_per_trade_pct')
        ->assertJsonPath('data.max_notional_usd', '500.00')
        ->assertJsonPath('data.notional_binding_limit', 'max_position_pct')
        ->assertJsonPath('data.used_exposure_today_usd', '0.00')
        ->assertJsonPath('data.trades_today', 0)
        ->assertJsonPath('data.reason', null)
        ->assertJsonPath('data.equity_as_of', fn (?string $v) => $v !== null && str_ends_with($v, 'Z'));

    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => ['bot_capital_cap_usdt' => null]])
        ->assertOk();

    $this->getJson('/api/settings/risk-preview?env=demo')
        ->assertOk()
        ->assertJsonPath('data.sizing_equity_usd', '5000000.00')
        ->assertJsonPath('data.cap_binds', false);
});

it('counts today\'s atlas entry orders toward used exposure and shrinks the daily-allowance term', function (): void {
    $this->patchJson('/api/settings', ['env' => 'demo', 'version' => 1, 'changes' => [
        'bot_capital_cap_usdt' => null, 'max_new_exposure_per_day_usdt' => '600', 'max_usdt_per_trade' => '10000',
    ]])->assertOk();
    seedRiskPreviewEquity('demo', '100000');
    seedRiskPreviewEntryOrder('demo', '500', CarbonImmutable::now('Asia/Baghdad'));
    // A different env's order must never count against this one's allowance.
    seedRiskPreviewEntryOrder('live', '999999', CarbonImmutable::now('Asia/Baghdad'));
    // Yesterday (Baghdad) does not count toward today's used exposure.
    seedRiskPreviewEntryOrder('demo', '777', CarbonImmutable::now('Asia/Baghdad')->subDays(2));

    $this->getJson('/api/settings/risk-preview?env=demo')
        ->assertOk()
        ->assertJsonPath('data.used_exposure_today_usd', '500.00')
        ->assertJsonPath('data.trades_today', 1)
        // max_position_pct (5% of 100000 = 5000) and max_usdt_per_trade (10000) both exceed the
        // 100 USD left of the 600 daily allowance after the 500 already used.
        ->assertJsonPath('data.max_notional_usd', '100.00')
        ->assertJsonPath('data.notional_binding_limit', 'max_new_exposure_per_day');
});
