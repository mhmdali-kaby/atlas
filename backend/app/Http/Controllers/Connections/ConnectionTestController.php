<?php

declare(strict_types=1);

namespace App\Http\Controllers\Connections;

use App\Http\Controllers\Controller;
use App\Http\Resources\ConnectionResource;
use App\Models\Connection;
use App\Support\Auditable;
use App\Support\TradingCore;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\JsonResponse;
use RuntimeException;

/**
 * Test connection (brief §14.2, §18; decisions #235, #237). trading_core decrypts the stored key,
 * calls OKX account/config and writes the verification columns; this endpoint relays its result,
 * adds the human message for the error slug, and audits the attempt. Only uid_last4 ever reaches
 * Laravel; the full UID stays inside trading_core.
 */
class ConnectionTestController extends Controller
{
    /** Fields relayed from trading_core's answer, in this order. */
    private const RESULT_FIELDS = [
        'ok', 'acct_lv', 'account_mode', 'position_mode', 'permissions', 'uid_last4', 'error', 'okx_code', 'verified_at',
    ];

    public function __construct(private readonly Auditable $audit) {}

    public function __invoke(Connection $connection): JsonResponse
    {
        try {
            $response = TradingCore::fromConfig()->post("/internal/connections/{$connection->id}/test");
        } catch (ConnectionException|RuntimeException) {
            return $this->failure(503, 'trading_core_unavailable');
        }

        if ($response->status() === 404) {
            return $this->failure(404, 'connection_not_found');
        }
        $body = $response->json();
        if (! $response->successful() || ! is_array($body) || ! array_key_exists('ok', $body)) {
            return $this->failure(502, 'trading_core_error');
        }

        $result = [];
        foreach (self::RESULT_FIELDS as $field) {
            $result[$field] = $body[$field] ?? null;
        }
        $result['ok'] = $result['ok'] === true;
        $result['permissions'] = is_array($result['permissions']) ? array_values($result['permissions']) : [];
        $result['message'] = $this->message($result['error']);

        $this->audit->record(
            action: 'connection.tested',
            subjectType: 'connections',
            subjectId: $connection->id,
            before: null,
            after: [
                ...$connection->auditSnapshot(),
                ...collect($result)->except(['verified_at', 'message'])->all(),
            ],
        );

        return response()->json([
            ...$result,
            'connection' => new ConnectionResource($connection->refresh()),
        ]);
    }

    private function failure(int $status, string $error): JsonResponse
    {
        return response()->json(['ok' => false, 'error' => $error, 'message' => $this->message($error)], $status);
    }

    private function message(mixed $error): ?string
    {
        if (! is_string($error) || $error === '') {
            return null;
        }
        $key = "connections_test.{$error}";
        $message = __($key);

        return is_string($message) && $message !== $key ? $message : __('connections_test.okx_error');
    }
}
