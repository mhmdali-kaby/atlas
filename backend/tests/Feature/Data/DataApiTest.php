<?php

declare(strict_types=1);

use App\Models\AuditLog;
use App\Models\BackfillJob;
use App\Models\DataQualityIssue;
use App\Models\User;
use App\Support\TradingCore;
use Carbon\CarbonImmutable;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\Str;

const ATL48_CORE = 'http://127.0.0.1:8100';
const ATL48_SECRET = 'atl48-test-only-internal-secret';

/**
 * @param  array<string, mixed>  $attrs
 */
function atl48Job(array $attrs): BackfillJob
{
    return BackfillJob::query()->create([
        'env' => 'demo',
        'status' => 'running',
        'rows_written' => 0,
        ...$attrs,
    ]);
}

function atl48Instrument(string $instId, string $assetClass, ?CarbonImmutable $listTime): void
{
    DB::table('instruments')->insert([
        'id' => (string) Str::uuid(),
        'env' => 'demo', 'inst_id' => $instId, 'inst_type' => 'SWAP',
        'lot_sz' => '1', 'min_sz' => '1', 'tick_sz' => '0.1', 'state' => 'live',
        'list_time' => $listTime, 'asset_class' => $assetClass,
        'created_at' => now(), 'updated_at' => now(),
    ]);
}

/**
 * @param  array<string, mixed>  $attrs
 */
function atl48Issue(array $attrs): DataQualityIssue
{
    return DataQualityIssue::query()->create([
        'env' => 'demo',
        'inst_id' => 'BTC-USDT-SWAP',
        'dataset' => 'candles:1H',
        'kind' => 'gap',
        'from_ts' => '2026-10-01T00:00:00Z',
        'to_ts' => '2026-10-01T05:00:00Z',
        'detail' => ['missing_bars' => 5],
        'detected_at' => now()->subDays(10),
        ...$attrs,
    ]);
}

beforeEach(function (): void {
    $this->now = CarbonImmutable::parse('2026-10-29T12:00:00Z');
    $this->travelTo($this->now);
    config(['services.trading_core.url' => ATL48_CORE, 'services.trading_core.secret' => ATL48_SECRET]);
    $this->user = User::factory()->create();
    $this->actingAs($this->user, 'web');
    RateLimiter::clear('data-resync|'.$this->user->id);
    RateLimiter::clear('data-pause|'.$this->user->id);
});

it('rejects guests on every data endpoint', function (): void {
    Http::fake();
    auth()->guard('web')->logout();

    $this->getJson('/api/data/backfill')->assertUnauthorized();
    $this->getJson('/api/data/storage')->assertUnauthorized();
    $this->postJson('/api/data/resync')->assertUnauthorized();
    $this->postJson('/api/data/pause')->assertUnauthorized();
    Http::assertNothingSent();
});

it('reports per-instrument datasets with percent, depth, target_from and totals', function (): void {
    atl48Instrument('BTC-USDT-SWAP', 'crypto', $this->now->subYears(6));
    atl48Instrument('XAU-USDT-SWAP', 'commodity', $this->now->subDays(100));

    // 730 days of 1H bars = 17,520 expected; 8,760 written = 50 %.
    atl48Job(['inst_id' => 'BTC-USDT-SWAP', 'dataset' => 'candles:1H', 'target_from' => $this->now->subDays(730),
        'cursor_ts' => $this->now->subDays(365), 'rows_written' => 8760]);
    // Full-history datasets: no estimate while running, rows = expected once done.
    atl48Job(['inst_id' => 'BTC-USDT-SWAP', 'dataset' => 'funding', 'target_from' => '2019-01-01T00:00:00Z', 'rows_written' => 300]);
    atl48Job(['inst_id' => 'BTC-USDT-SWAP', 'dataset' => 'open_interest', 'target_from' => '2019-01-01T00:00:00Z',
        'rows_written' => 1200, 'status' => 'done']);
    atl48Job(['inst_id' => 'BTC-USDT-SWAP', 'dataset' => 'candles:1D', 'target_from' => '2019-01-01T00:00:00Z', 'status' => 'pending']);
    // Listed 100 days ago: expected is bounded by the listing (2,400 bars), so 600 written = 25 %.
    atl48Job(['inst_id' => 'XAU-USDT-SWAP', 'dataset' => 'candles:1H', 'target_from' => $this->now->subDays(730),
        'rows_written' => 600, 'status' => 'failed', 'last_error' => 'okx_unreachable']);
    // Another env never leaks in.
    atl48Job(['env' => 'live', 'inst_id' => 'ETH-USDT-SWAP', 'dataset' => 'candles:1H', 'target_from' => $this->now->subDays(730)]);
    atl48Issue(['detected_at' => $this->now->subHour()]);

    $res = $this->getJson('/api/data/backfill?env=demo')->assertOk();

    $res->assertJsonPath('data.env', 'demo')
        ->assertJsonCount(2, 'data.instruments')
        ->assertJsonPath('data.instruments.0.inst_id', 'BTC-USDT-SWAP')
        ->assertJsonPath('data.instruments.0.asset_class', 'crypto')
        ->assertJsonPath('data.instruments.0.open_issues', 1)
        // Datasets come in the fixed order candles 1m…1D, funding, open_interest, …
        ->assertJsonPath('data.instruments.0.datasets.*.dataset', ['candles:1H', 'candles:1D', 'funding', 'open_interest']);

    $btc = collect($res->json('data.instruments.0.datasets'))->keyBy('dataset');
    expect($btc['candles:1H'])->toMatchArray([
        'status' => 'running', 'depth_days' => 730, 'target_from' => '2024-10-29T12:00:00.000000Z',
        'cursor_ts' => '2025-10-29T12:00:00.000000Z', 'rows_written' => 8760, 'expected' => 17520, 'percent' => '50.0',
        'last_error' => null,
    ]);
    expect($btc['funding'])->toMatchArray(['depth_days' => null, 'target_from' => null, 'expected' => null, 'percent' => null, 'rows_written' => 300]);
    expect($btc['candles:1D'])->toMatchArray(['depth_days' => null, 'target_from' => null, 'expected' => null, 'percent' => null]);
    expect($btc['open_interest'])->toMatchArray(['status' => 'done', 'depth_days' => null, 'expected' => 1200, 'percent' => '100.0']);

    $res->assertJsonPath('data.instruments.1.inst_id', 'XAU-USDT-SWAP')
        ->assertJsonPath('data.instruments.1.asset_class', 'commodity')
        ->assertJsonPath('data.instruments.1.datasets.0.expected', 2400)
        ->assertJsonPath('data.instruments.1.datasets.0.percent', '25.0')
        ->assertJsonPath('data.instruments.1.datasets.0.status', 'failed')
        ->assertJsonPath('data.instruments.1.datasets.0.last_error', 'okx_unreachable');

    $res->assertJsonPath('data.totals.instruments', 2)
        ->assertJsonPath('data.totals.jobs', 5)
        ->assertJsonPath('data.totals.by_status', ['pending' => 1, 'running' => 2, 'done' => 1, 'failed' => 1, 'paused' => 0])
        ->assertJsonPath('data.totals.rows_written', 8760 + 300 + 1200 + 600)
        ->assertJsonPath('data.totals.percent', '20.0')
        ->assertJsonPath('data.quality.open', 1)
        ->assertJsonPath('data.quality.open_by_kind.gap', 1);
});

it('lists open issues and those repaired in the last 7 days, not older ones', function (): void {
    $open = atl48Issue(['kind' => 'gap', 'detected_at' => $this->now->subDays(1)]);
    $recent = atl48Issue(['kind' => 'outlier', 'dataset' => 'candles:1m', 'from_ts' => '2026-10-02T00:00:00Z',
        'to_ts' => '2026-10-02T00:01:00Z', 'detail' => ['sigma' => 9.2],
        'detected_at' => $this->now->subDays(4), 'repaired_at' => $this->now->subDays(3)]);
    atl48Issue(['kind' => 'duplicate', 'from_ts' => '2026-10-03T00:00:00Z',
        'detected_at' => $this->now->subDays(9), 'repaired_at' => $this->now->subDays(8)]);
    atl48Issue(['env' => 'live', 'kind' => 'zero_volume']);

    $res = $this->getJson('/api/data/backfill')->assertOk();

    $res->assertJsonCount(2, 'data.quality_issues')
        ->assertJsonPath('data.quality_issues.0.id', $open->id)
        ->assertJsonPath('data.quality_issues.1.id', $recent->id)
        ->assertJsonPath('data.quality.open', 1);
    expect($res->json('data.quality_issues.1'))->toBe([
        'id' => $recent->id,
        'kind' => 'outlier',
        'inst_id' => 'BTC-USDT-SWAP',
        'dataset' => 'candles:1m',
        'from_ts' => '2026-10-02T00:00:00.000000Z',
        'to_ts' => '2026-10-02T00:01:00.000000Z',
        'detected_at' => '2026-10-25T12:00:00.000000Z',
        'repaired_at' => '2026-10-26T12:00:00.000000Z',
        'detail' => ['sigma' => 9.2],
    ]);
});

it('validates env', function (): void {
    $this->getJson('/api/data/backfill?env=prod')->assertUnprocessable()->assertJsonValidationErrors('env');
});

it('reports the on-disk size of each hypertable and the total', function (): void {
    DB::table('candles')->insert([
        'env' => 'demo', 'inst_id' => 'BTC-USDT-SWAP', 'bar' => '1H', 'ts' => '2026-10-29T00:00:00Z',
        'o' => '1', 'h' => '1', 'l' => '1', 'c' => '1', 'vol' => '1',
    ]);

    $res = $this->getJson('/api/data/storage')->assertOk();

    $res->assertJsonPath('data.hypertables.*.name', ['candles', 'funding_rates', 'open_interest', 'long_short_ratio', 'taker_volume']);
    $sizes = collect($res->json('data.hypertables'))->pluck('bytes', 'name');
    expect($sizes->every(fn ($b) => is_int($b) && $b >= 0))->toBeTrue()
        ->and($sizes['candles'])->toBeGreaterThan(0)
        ->and($res->json('data.total_bytes'))->toBe($sizes->sum());
});

it('sends one signed resync call to trading_core and audits it', function (): void {
    Http::fake([ATL48_CORE.'/internal/backfill/start' => Http::response(['ok' => true, 'jobs' => 3])]);

    $this->postJson('/api/data/resync', ['env' => 'demo', 'inst_id' => 'BTC-USDT-SWAP'])
        ->assertStatus(202)
        ->assertJsonPath('ok', true)
        ->assertJsonPath('inst_id', 'BTC-USDT-SWAP')
        ->assertJsonPath('trading_core.jobs', 3);

    Http::assertSentCount(1);
    Http::assertSent(function (Request $request): bool {
        $ts = $request->header(TradingCore::TIMESTAMP_HEADER)[0] ?? '';
        $nonce = $request->header(TradingCore::NONCE_HEADER)[0] ?? '';
        $expected = TradingCore::sign(ATL48_SECRET, $ts, $nonce, 'POST', '/internal/backfill/start', $request->body());

        return $request->url() === ATL48_CORE.'/internal/backfill/start'
            && $request->method() === 'POST'
            && $request['env'] === 'demo' && $request['inst_id'] === 'BTC-USDT-SWAP'
            && $ts !== '' && preg_match('/^[0-9a-f]{32}$/', $nonce) === 1
            && hash_equals($expected, $request->header(TradingCore::SIGNATURE_HEADER)[0] ?? '');
    });

    $audit = AuditLog::query()->where('action', 'data.resync_requested')->sole();
    expect($audit->user_id)->toBe($this->user->id)
        ->and($audit->subject_type)->toBe('backfill_jobs')
        ->and($audit->after)->toMatchArray(['env' => 'demo', 'inst_id' => 'BTC-USDT-SWAP', 'ok' => true]);
});

it('returns 502 with a translated message when trading_core fails', function (): void {
    Http::fake([ATL48_CORE.'/*' => Http::response(['detail' => 'boom'], 500)]);

    $this->postJson('/api/data/resync')
        ->assertStatus(502)
        ->assertJsonPath('ok', false)
        ->assertJsonPath('error', 'trading_core_error')
        ->assertJsonPath('message', __('data.trading_core_error'));
    expect(__('data.trading_core_error'))->not->toBe('data.trading_core_error');
    expect(AuditLog::query()->where('action', 'data.resync_requested')->sole()->after)
        ->toMatchArray(['ok' => false, 'error' => 'trading_core_error', 'inst_id' => null]);
});

it('returns 502 when trading_core is unreachable', function (): void {
    Http::fake(fn () => throw new ConnectionException('refused'));

    $this->postJson('/api/data/resync')
        ->assertStatus(502)
        ->assertJsonPath('error', 'trading_core_unavailable')
        ->assertJsonPath('message', __('data.trading_core_unavailable'));
});

it('rate-limits a second resync inside the window', function (): void {
    Http::fake([ATL48_CORE.'/*' => Http::response(['ok' => true])]);

    $this->postJson('/api/data/resync')->assertStatus(202);
    $this->postJson('/api/data/resync')->assertStatus(429);
    Http::assertSentCount(1);

    $this->travel(61)->seconds();
    $this->postJson('/api/data/resync')->assertStatus(202);
});

it('relays pause to trading_core', function (): void {
    Http::fake([ATL48_CORE.'/internal/backfill/pause' => Http::response(['ok' => true])]);

    $this->postJson('/api/data/pause', ['inst_id' => 'XAU-USDT-SWAP'])->assertStatus(202);

    Http::assertSent(fn (Request $r): bool => $r->url() === ATL48_CORE.'/internal/backfill/pause'
        && $r['inst_id'] === 'XAU-USDT-SWAP' && $r->hasHeader(TradingCore::SIGNATURE_HEADER));
    expect(AuditLog::query()->where('action', 'data.pause_requested')->count())->toBe(1);
});

it('rejects a malformed inst_id', function (): void {
    Http::fake();

    $this->postJson('/api/data/resync', ['inst_id' => '../etc'])->assertUnprocessable();
    Http::assertNothingSent();
});
