<?php

declare(strict_types=1);

namespace App\Http\Controllers\Notifications;

use App\Http\Controllers\Controller;
use App\Http\Requests\Notifications\StoreTelegramRequest;
use App\Http\Resources\TelegramConnectionResource;
use App\Models\TelegramConnection;
use App\Support\Auditable;
use App\Support\Telegram\TelegramClient;
use App\Support\Telegram\TelegramException;
use App\Support\Telegram\TelegramMessages;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;

/**
 * /api/notifications/telegram (ATL-215, brief §14.9, decision #254 (3)). The token and chat id go in
 * encrypted and only ever come back as their last 4 characters; every write is audited the same way.
 * Which events go to Telegram is the notifications.<event>.telegram settings keys (ATL-214).
 */
class TelegramController extends Controller
{
    public function __construct(private readonly Auditable $audit) {}

    public function show(): JsonResponse
    {
        return response()->json(['data' => (new TelegramConnectionResource(TelegramConnection::current()))->resolve()]);
    }

    public function update(StoreTelegramRequest $request): JsonResponse
    {
        $token = $request->botToken();
        $chatId = $request->chatId();

        $connection = DB::transaction(function () use ($token, $chatId): TelegramConnection {
            $existing = TelegramConnection::query()->lockForUpdate()->where('singleton', true)->first();
            $before = $existing?->auditSnapshot();
            $connection = $existing ?? new TelegramConnection;
            $tokenChanged = $existing === null || ! hash_equals($existing->bot_token, $token);
            $connection->fill([
                'bot_token' => $token,
                'chat_id' => $chatId,
                'bot_token_last4' => mb_substr($token, -4),
                'chat_id_last4' => mb_substr($chatId, -4),
                'last_error' => null,
            ]);
            if ($tokenChanged) {
                // A different bot has its own update ids.
                $connection->update_offset = null;
            }
            $connection->save();
            $this->audit->record(
                $existing === null ? 'telegram.created' : 'telegram.updated',
                'telegram_connections',
                $connection->id,
                $before,
                [...$connection->auditSnapshot(), 'token_changed' => $tokenChanged],
            );

            return $connection;
        });

        return response()->json(['data' => (new TelegramConnectionResource($connection))->resolve()], $connection->wasRecentlyCreated ? 201 : 200);
    }

    public function destroy(): JsonResponse
    {
        DB::transaction(function (): void {
            $existing = TelegramConnection::query()->lockForUpdate()->where('singleton', true)->first();
            if ($existing === null) {
                return;
            }
            $before = $existing->auditSnapshot();
            $existing->delete();
            $this->audit->record('telegram.deleted', 'telegram_connections', $existing->id, $before, null);
        });

        return response()->json(['data' => (new TelegramConnectionResource(null))->resolve()]);
    }

    public function test(): JsonResponse
    {
        $connection = TelegramConnection::current();
        if ($connection === null) {
            return response()->json(['ok' => false, 'error' => 'not_configured', 'message' => __('telegram.not_configured')], 409);
        }

        $ok = true;
        try {
            TelegramClient::for($connection)->sendMessage($connection->chat_id, (string) __('telegram.test_message', [], TelegramMessages::ownerLocale()));
            $connection->forceFill(['last_tested_at' => Carbon::now(), 'last_error' => null])->save();
        } catch (TelegramException $e) {
            $ok = false;
            $connection->forceFill(['last_error' => mb_substr($e->getMessage(), 0, 300)])->save();
            Log::warning('atlas.telegram.test_failed', ['http_status' => $e->httpStatus, 'error_code' => $e->errorCode]);
        }
        $this->audit->record('telegram.tested', 'telegram_connections', $connection->id, null, ['ok' => $ok, ...$connection->auditSnapshot()]);

        return response()->json([
            'ok' => $ok,
            'error' => $ok ? null : 'test_failed',
            'message' => $ok ? null : __('telegram.test_failed'),
            'data' => (new TelegramConnectionResource($connection->refresh()))->resolve(),
        ], $ok ? 200 : 502);
    }
}
