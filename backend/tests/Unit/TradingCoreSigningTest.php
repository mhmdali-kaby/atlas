<?php

declare(strict_types=1);

use App\Support\TradingCore;
use Illuminate\Http\Client\Factory as HttpFactory;
use Illuminate\Http\Client\Request;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

uses(TestCase::class);

/**
 * @return array{secret: string, timestamp: string, method: string, path: string, body: string, signature: string}
 */
function internalHmacVector(): array
{
    /** @var array{secret: string, timestamp: string, method: string, path: string, body: string, signature: string} $vector */
    $vector = json_decode(
        (string) file_get_contents(dirname(base_path()).'/test-vectors/internal_hmac.json'),
        true,
        flags: JSON_THROW_ON_ERROR,
    );

    return $vector;
}

it('produces the shared test-vector signature that pytest verifies', function (): void {
    $v = internalHmacVector();

    expect(TradingCore::sign($v['secret'], $v['timestamp'], $v['method'], $v['path'], $v['body']))
        ->toBe($v['signature']);
});

it('sends the shared test-vector request with exactly that signature', function (): void {
    $v = internalHmacVector();
    Http::fake(['*' => Http::response(['ok' => true])]);

    $client = new TradingCore(
        app(HttpFactory::class),
        'http://127.0.0.1:8100/',
        $v['secret'],
        clock: static fn (): int => (int) $v['timestamp'],
    );
    $response = $client->send($v['method'], $v['path'], $v['body']);

    expect($response->json('ok'))->toBeTrue();
    Http::assertSent(function (Request $request) use ($v): bool {
        return $request->method() === 'POST'
            && $request->url() === 'http://127.0.0.1:8100'.$v['path']
            && $request->body() === $v['body']
            && $request->header(TradingCore::TIMESTAMP_HEADER) === [$v['timestamp']]
            && $request->header(TradingCore::SIGNATURE_HEADER) === [$v['signature']];
    });
});

it('signs GET query strings and POST JSON bodies exactly as they go on the wire', function (): void {
    Http::fake(['*' => Http::response(['ok' => true])]);
    $client = new TradingCore(app(HttpFactory::class), 'http://127.0.0.1:8100', 's3cret', clock: static fn (): int => 1_700_000_000);

    $client->get('/internal/ping', ['env' => 'demo']);
    $client->post('/internal/sync', ['kind' => 'balances']);

    Http::assertSent(function (Request $request): bool {
        $parts = parse_url($request->url());
        $path = ($parts['path'] ?? '').(isset($parts['query']) ? '?'.$parts['query'] : '');
        $expected = TradingCore::sign('s3cret', '1700000000', $request->method(), $path, $request->body());

        return $request->header(TradingCore::SIGNATURE_HEADER) === [$expected];
    });
    Http::assertSentCount(2);
});

it('refuses to call trading_core without a shared secret', function (): void {
    $client = new TradingCore(app(HttpFactory::class), 'http://127.0.0.1:8100', '');

    expect(fn () => $client->get('/internal/ping'))->toThrow(RuntimeException::class);
});

it('reads its base URL and secret from config', function (): void {
    config(['services.trading_core.url' => 'http://127.0.0.1:9999', 'services.trading_core.secret' => 'from-config']);
    Http::fake(['*' => Http::response(['ok' => true])]);

    TradingCore::fromConfig()->get('/internal/ping');

    Http::assertSent(fn (Request $request): bool => str_starts_with($request->url(), 'http://127.0.0.1:9999/internal/ping'));
});
