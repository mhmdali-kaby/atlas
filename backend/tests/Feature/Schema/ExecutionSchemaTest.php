<?php

declare(strict_types=1);

use Illuminate\Database\QueryException;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Str;

/** ATL-141: orders, algo_orders, fills and trading_state (brief §5, §11). */
function execProposalAndCheck(): array
{
    $proposal = (string) Str::uuid();
    DB::table('proposals')->insert([
        'id' => $proposal, 'env' => 'demo', 'author' => 'strategist', 'inst_id' => 'BTC-USDT-SWAP',
        'side' => 'buy', 'order_type' => 'limit', 'entry' => '68000.5', 'stop' => '66900',
    ]);
    $check = (string) Str::uuid();
    DB::table('risk_checks')->insert([
        'id' => $check, 'env' => 'demo', 'proposal_id' => $proposal, 'settings_version' => 1,
        'result' => 'approved', 'decision' => 'send_now', 'final_size' => '0.12',
    ]);

    return [$proposal, $check];
}

/** @param array<string, mixed> $overrides */
function execOrder(array $overrides = []): string
{
    $id = (string) Str::uuid();
    DB::table('orders')->insert(array_merge([
        'id' => $id, 'env' => 'demo', 'inst_id' => 'BTC-USDT-SWAP', 'side' => 'buy', 'pos_side' => 'net',
        'ord_type' => 'limit', 'td_mode' => 'isolated', 'px' => '68000.5', 'sz' => '0.12',
        'origin' => 'external', 'purpose' => 'entry', 'cl_ord_id' => 'atl'.Str::lower(Str::random(20)),
    ], $overrides));

    return $id;
}

it('creates orders, algo_orders, fills and trading_state', function (): void {
    expect(Schema::hasColumns('orders', [
        'env', 'proposal_id', 'risk_check_id', 'cl_ord_id', 'ord_id', 'inst_id', 'side', 'pos_side', 'ord_type',
        'td_mode', 'px', 'sz', 'filled_sz', 'avg_px', 'state', 'reduce_only', 'origin', 'purpose',
        'last_error_code', 'last_error_msg', 'raw', 'created_at', 'updated_at',
    ]))->toBeTrue()
        ->and(Schema::hasColumns('algo_orders', [
            'env', 'order_id', 'algo_id', 'algo_cl_ord_id', 'inst_id', 'kind', 'trigger_px', 'sl_trigger_px',
            'tp_trigger_px', 'sz', 'state', 'origin',
        ]))->toBeTrue()
        ->and(Schema::hasColumns('fills', [
            'env', 'order_id', 'trade_id', 'ord_id', 'inst_id', 'side', 'fill_px', 'fill_sz', 'fee', 'fee_ccy', 'ts', 'raw',
        ]))->toBeTrue()
        ->and(Schema::hasColumns('trading_state', ['env', 'paused', 'reason', 'breakers']))->toBeTrue();
});

it('refuses a second order with the same cl_ord_id', function (): void {
    execOrder(['cl_ord_id' => 'atl0123456789abcdef']);

    expect(fn () => execOrder(['cl_ord_id' => 'atl0123456789abcdef']))
        ->toThrow(UniqueConstraintViolationException::class);
});

it('accepts every executor state and refuses anything else', function (string $state): void {
    $id = execOrder(['state' => $state]);

    expect(DB::table('orders')->where('id', $id)->value('state'))->toBe($state);
})->with(['new', 'sending', 'live', 'partially_filled', 'filled', 'canceled', 'rejected', 'failed']);

it('refuses an unknown order state, origin or purpose', function (array $bad): void {
    expect(fn () => execOrder($bad))->toThrow(QueryException::class);
})->with([
    'state' => [['state' => 'pending']],
    'origin' => [['origin' => 'agent']],
    'purpose' => [['purpose' => 'hedge']],
]);

it('keeps sizes exact as numeric and refuses a fill above the order size', function (): void {
    $id = execOrder(['sz' => '0.000000000000000001']);
    expect((string) DB::table('orders')->where('id', $id)->value('sz'))->toBe('0.000000000000000001');

    expect(fn () => execOrder(['sz' => '1', 'filled_sz' => '2']))->toThrow(QueryException::class);
});

it('refuses an Atlas entry order without a proposal and a risk check (rule 1)', function (): void {
    expect(fn () => DB::transaction(fn () => execOrder(['origin' => 'atlas'])))->toThrow(QueryException::class);

    [$proposal, $check] = execProposalAndCheck();
    $id = execOrder(['origin' => 'atlas', 'proposal_id' => $proposal, 'risk_check_id' => $check]);
    expect(DB::table('orders')->where('id', $id)->value('origin'))->toBe('atlas');
});

it('refuses an Atlas order with no clOrdId but imports an external one without it', function (): void {
    expect(fn () => DB::transaction(fn () => execOrder(['origin' => 'atlas', 'purpose' => 'kill', 'cl_ord_id' => null])))
        ->toThrow(QueryException::class);

    $id = execOrder(['cl_ord_id' => null, 'ord_id' => '1234567890']);
    expect(DB::table('orders')->where('id', $id)->value('cl_ord_id'))->toBeNull();
});

it('enforces unique algo_cl_ord_id and an OCO with both triggers', function (): void {
    $order = execOrder();
    $row = [
        'env' => 'demo', 'order_id' => $order, 'inst_id' => 'BTC-USDT-SWAP', 'kind' => 'sl',
        'trigger_px' => '66900', 'algo_cl_ord_id' => 'atlsl01', 'origin' => 'atlas',
    ];
    DB::table('algo_orders')->insert(['id' => (string) Str::uuid(), ...$row]);

    expect(fn () => DB::transaction(fn () => DB::table('algo_orders')->insert(['id' => (string) Str::uuid(), ...$row])))
        ->toThrow(UniqueConstraintViolationException::class);
    expect(fn () => DB::table('algo_orders')->insert([
            'id' => (string) Str::uuid(), ...$row, 'algo_cl_ord_id' => 'atloco01', 'kind' => 'oco',
            'trigger_px' => null, 'sl_trigger_px' => '66900',
    ]))->toThrow(QueryException::class);
});

it('keeps one fill per trade_id per instrument', function (): void {
    $fill = [
        'env' => 'demo', 'trade_id' => '987', 'ord_id' => '1234567890', 'inst_id' => 'BTC-USDT-SWAP',
        'side' => 'buy', 'fill_px' => '68000.5', 'fill_sz' => '0.12', 'fee' => '-0.4', 'fee_ccy' => 'USDT',
        'ts' => '2026-10-30T08:00:00Z',
    ];
    DB::table('fills')->insert(['id' => (string) Str::uuid(), ...$fill]);
    DB::table('fills')->insert(['id' => (string) Str::uuid(), ...$fill, 'inst_id' => 'ETH-USDT-SWAP']);

    expect(fn () => DB::table('fills')->insert(['id' => (string) Str::uuid(), ...$fill]))
        ->toThrow(UniqueConstraintViolationException::class);
});

it('keeps one trading_state row per env', function (): void {
    DB::table('trading_state')->insert(['id' => (string) Str::uuid(), 'env' => 'demo']);

    expect(DB::table('trading_state')->where('env', 'demo')->value('paused'))->toBeFalse()
        ->and(fn () => DB::table('trading_state')->insert(['id' => (string) Str::uuid(), 'env' => 'demo']))
        ->toThrow(UniqueConstraintViolationException::class);
});
