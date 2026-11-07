<?php

declare(strict_types=1);

namespace App\Support\Telegram;

use RuntimeException;

/**
 * A failed Bot API call. Built from scratch by TelegramClient with the token scrubbed out and no
 * previous exception chained, so neither the message nor a chained cause (which for a network error
 * holds the request URL https://api.telegram.org/bot<token>/...) can carry the token into a log,
 * failed_jobs or a response.
 */
final class TelegramException extends RuntimeException
{
    public function __construct(
        string $message,
        public readonly string $method,
        public readonly ?int $httpStatus = null,
        public readonly ?int $errorCode = null,
    ) {
        parent::__construct($message);
    }
}
