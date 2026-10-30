<?php

declare(strict_types=1);

namespace App\Support\System;

use App\Support\TradingCore;
use Illuminate\Http\Client\ConnectionException;
use RuntimeException;

/**
 * One signed POST to a trading_core control route (ATL-147: pause, resume, kill, position close,
 * order cancel) and what the owner's API answers with it (ATL-148).
 *
 * - trading_core answered 200 / 404 / 409 / 422 / 502 with a JSON object: that status and body pass
 *   through unchanged (the kill report, `no_position`, `order_not_open`, a failed close, ...).
 * - trading_core is down, or INTERNAL_SHARED_SECRET is not set: 503 `trading_core_unavailable`.
 * - anything else (401 bad signature, 500, a non-JSON body): 502 `trading_core_error`.
 */
final class ControlCall
{
    /** trading_core statuses whose JSON body is a meaningful answer for the owner. */
    private const PASS_THROUGH = [200, 404, 409, 422, 502];

    /**
     * @param  array<string, mixed>  $body  the trading_core JSON (or our own error body)
     */
    private function __construct(
        public readonly int $status,
        public readonly array $body,
        public readonly ?string $error,
    ) {}

    /**
     * @param  array<string, mixed>  $data
     */
    public static function post(string $path, array $data = []): self
    {
        try {
            $response = TradingCore::fromConfig()->post($path, $data);
        } catch (ConnectionException|RuntimeException) {
            return self::failure(503, 'trading_core_unavailable');
        }

        $json = $response->json();
        if (! is_array($json) || ! in_array($response->status(), self::PASS_THROUGH, true)) {
            return self::failure(502, 'trading_core_error');
        }
        /** @var array<string, mixed> $json */
        $error = $response->status() === 200 ? null : (is_string($json['error'] ?? null) ? $json['error'] : 'trading_core_error');

        return new self($response->status(), $json, $error);
    }

    private static function failure(int $status, string $error): self
    {
        return new self($status, ['ok' => false, 'error' => $error, 'message' => __("system.{$error}")], $error);
    }

    /**
     * The audit_log `after` for this call: outcome plus the report's counts, never the raw items list
     * twice. Holds no secrets (trading_core never returns any).
     *
     * @return array<string, mixed>
     */
    public function auditOutcome(): array
    {
        $keep = ['ok', 'error', 'status', 'paused', 'reason', 'flatten', 'cancelled', 'cancelled_orders',
            'cancelled_algos', 'already_done', 'closed', 'stops_kept', 'failed', 'active_breakers',
            'breakers_cleared', 'inst_id', 'order_id', 'filled_sz'];
        $out = ['http_status' => $this->status];
        foreach ($keep as $key) {
            if (array_key_exists($key, $this->body)) {
                $out[$key === 'status' ? 'result' : $key] = $this->body[$key];
            }
        }
        $out['error'] = $this->error;

        return $out;
    }
}
