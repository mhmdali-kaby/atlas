<?php

declare(strict_types=1);

namespace App\Support;

use Illuminate\Http\Client\Factory as HttpFactory;
use Illuminate\Http\Client\Response;
use InvalidArgumentException;
use RuntimeException;

/**
 * HMAC-signed client for trading_core's internal API (decision #235).
 *
 * Signature = hex HMAC-SHA256(INTERNAL_SHARED_SECRET, timestamp + nonce + METHOD + path + raw body),
 * sent as X-Atlas-Signature with X-Atlas-Timestamp (unix seconds) and X-Atlas-Nonce (32 lower-case hex
 * chars, fresh per call). `path` includes `?query` when there is one. trading_core accepts each nonce
 * once, so a captured request cannot be replayed (ATL-34).
 * The Python verifier is services/trading_core/src/trading_core/security.py; both are pinned to
 * test-vectors/internal_hmac.json. Never log the secret, the signature or the body.
 */
final class TradingCore
{
    public const TIMESTAMP_HEADER = 'X-Atlas-Timestamp';

    public const SIGNATURE_HEADER = 'X-Atlas-Signature';

    public const NONCE_HEADER = 'X-Atlas-Nonce';

    /** @var \Closure(): int */
    private \Closure $clock;

    /** @var \Closure(): string */
    private \Closure $nonce;

    /**
     * @param  (\Closure(): int)|null  $clock  unix-seconds clock; injectable for tests
     * @param  (\Closure(): string)|null  $nonce  nonce source; injectable for tests
     */
    public function __construct(
        private readonly HttpFactory $http,
        private readonly string $baseUrl,
        private readonly string $secret,
        private readonly int $timeoutSeconds = 10,
        ?\Closure $clock = null,
        ?\Closure $nonce = null,
    ) {
        $this->clock = $clock ?? static fn (): int => time();
        $this->nonce = $nonce ?? static fn (): string => bin2hex(random_bytes(16));
    }

    public static function fromConfig(): self
    {
        $url = config('services.trading_core.url');
        $secret = config('services.trading_core.secret');
        $timeout = config('services.trading_core.timeout');

        return new self(
            app(HttpFactory::class),
            is_string($url) ? $url : 'http://127.0.0.1:8100',
            is_string($secret) ? $secret : '',
            is_int($timeout) ? $timeout : 10,
        );
    }

    public static function sign(string $secret, string $timestamp, string $nonce, string $method, string $path, string $body): string
    {
        return hash_hmac('sha256', $timestamp.$nonce.strtoupper($method).$path.$body, $secret);
    }

    /**
     * @param  array<string, scalar>  $query
     */
    public function get(string $path, array $query = []): Response
    {
        $qs = $query === [] ? '' : '?'.http_build_query($query, '', '&', PHP_QUERY_RFC3986);

        return $this->send('GET', $path.$qs, '');
    }

    /**
     * @param  array<string, mixed>  $data
     */
    public function post(string $path, array $data = []): Response
    {
        $body = json_encode($data === [] ? new \stdClass : $data, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES);

        return $this->send('POST', $path, $body);
    }

    /**
     * Send a signed request. `$pathWithQuery` must start with `/` and is signed exactly as sent.
     */
    public function send(string $method, string $pathWithQuery, string $body): Response
    {
        if (! str_starts_with($pathWithQuery, '/')) {
            throw new InvalidArgumentException('trading_core paths must start with "/".');
        }
        if ($this->secret === '') {
            throw new RuntimeException('INTERNAL_SHARED_SECRET is not set; trading_core would reject the call.');
        }

        $method = strtoupper($method);
        $timestamp = (string) ($this->clock)();
        $nonce = ($this->nonce)();

        $request = $this->http
            ->baseUrl(rtrim($this->baseUrl, '/'))
            ->timeout($this->timeoutSeconds)
            ->acceptJson()
            ->withHeaders([
                self::TIMESTAMP_HEADER => $timestamp,
                self::NONCE_HEADER => $nonce,
                self::SIGNATURE_HEADER => self::sign($this->secret, $timestamp, $nonce, $method, $pathWithQuery, $body),
            ]);

        if ($body !== '') {
            $request = $request->withBody($body, 'application/json');
        }

        return $request->send($method, $pathWithQuery);
    }
}
