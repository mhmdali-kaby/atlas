<?php

declare(strict_types=1);

namespace App\Support\Telegram;

use App\Models\TelegramConnection;
use Illuminate\Http\Client\Factory as HttpFactory;

/**
 * Minimal Telegram Bot API client (ATL-215) on Laravel's HTTP client: sendMessage, getUpdates,
 * answerCallbackQuery and editMessageText. Outgoing calls only — there is no webhook (brief §18).
 *
 * The token lives only in a private property and in the request URL. Every failure (network,
 * HTTP or `ok: false`) is rethrown as a TelegramException whose message has the token removed and
 * which chains nothing, and no method takes the token as an argument, so it cannot reach a log, an
 * exception trace or failed_jobs.
 */
final class TelegramClient
{
    public function __construct(
        private readonly HttpFactory $http,
        #[\SensitiveParameter] private readonly string $token,
        private readonly string $apiBase,
    ) {}

    public static function for(TelegramConnection $connection): self
    {
        $base = config('atlas_defaults.telegram.api_base');

        return new self(app(HttpFactory::class), $connection->bot_token, is_string($base) ? $base : 'https://api.telegram.org');
    }

    /**
     * @param  array<string, mixed>|null  $replyMarkup
     * @return array<string, mixed> the sent Message
     */
    public function sendMessage(string $chatId, string $text, ?array $replyMarkup = null): array
    {
        $params = ['chat_id' => $chatId, 'text' => $text, 'disable_web_page_preview' => true];
        if ($replyMarkup !== null) {
            $params['reply_markup'] = $replyMarkup;
        }
        $result = $this->call('sendMessage', $params, self::seconds('send_timeout_seconds', 10));

        return is_array($result) ? $result : [];
    }

    /**
     * Long poll. Returns the updates after `$offset` (Telegram's offset = last update_id + 1).
     *
     * @return list<array<string, mixed>>
     */
    public function getUpdates(?int $offset, int $timeout): array
    {
        $params = ['timeout' => $timeout, 'allowed_updates' => ['callback_query']];
        if ($offset !== null) {
            $params['offset'] = $offset;
        }
        $result = $this->call('getUpdates', $params, self::seconds('http_timeout_seconds', $timeout + 10));

        return is_array($result) ? array_values(array_filter($result, 'is_array')) : [];
    }

    public function answerCallbackQuery(string $callbackQueryId, string $text): void
    {
        $this->call('answerCallbackQuery', ['callback_query_id' => $callbackQueryId, 'text' => $text], self::seconds('send_timeout_seconds', 10));
    }

    /**
     * Replace a sent message's text and drop its buttons.
     */
    public function editMessageText(string $chatId, int $messageId, string $text): void
    {
        $this->call('editMessageText', [
            'chat_id' => $chatId,
            'message_id' => $messageId,
            'text' => $text,
            'reply_markup' => ['inline_keyboard' => []],
        ], self::seconds('send_timeout_seconds', 10));
    }

    /**
     * @param  array<string, mixed>  $params
     */
    private function call(string $method, array $params, int $timeout): mixed
    {
        try {
            $response = $this->http->timeout($timeout)->acceptJson()->asJson()
                ->post(rtrim($this->apiBase, '/').'/bot'.$this->token.'/'.$method, $params);
        } catch (\Throwable $e) {
            throw new TelegramException($this->scrub("Telegram {$method} failed: ".$e::class), $method);
        }

        $json = $response->json();
        if (! $response->successful() || ! is_array($json) || ($json['ok'] ?? false) !== true) {
            $description = is_array($json) && is_string($json['description'] ?? null) ? $json['description'] : 'no description';
            $code = is_array($json) && is_int($json['error_code'] ?? null) ? $json['error_code'] : null;

            throw new TelegramException(
                $this->scrub(mb_substr("Telegram {$method} failed ({$response->status()}): {$description}", 0, 300)),
                $method,
                $response->status(),
                $code,
            );
        }

        return $json['result'] ?? null;
    }

    private function scrub(string $text): string
    {
        return $this->token === '' ? $text : str_replace($this->token, '[redacted]', $text);
    }

    private static function seconds(string $key, int $fallback): int
    {
        $value = config("atlas_defaults.telegram.{$key}");

        return is_int($value) ? $value : $fallback;
    }
}
