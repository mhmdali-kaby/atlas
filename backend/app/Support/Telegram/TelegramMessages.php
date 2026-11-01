<?php

declare(strict_types=1);

namespace App\Support\Telegram;

use App\Models\Notification;
use App\Models\Proposal;
use App\Models\User;
use Illuminate\Support\Carbon;

/**
 * Turns a stored notification into Telegram text (ATL-215) from lang/<locale>/telegram.php, in the
 * owner's language (users.locale), plus the Approve / Reject keyboard for a proposal that is still
 * waiting for the owner. Plain text only: params (e.g. an untrusted headline) are never markup.
 */
final class TelegramMessages
{
    /** Params holding a UTC instant, shown in the owner's timezone. */
    private const TIME_PARAMS = ['approval_expires_at', 'since', 'resets_at'];

    public static function ownerLocale(): string
    {
        $locale = User::query()->orderBy('created_at')->value('locale');

        return in_array($locale, ['en', 'ar'], true) ? $locale : 'en';
    }

    public static function text(Notification $notification, string $locale): string
    {
        $params = [];
        foreach ($notification->params as $key => $value) {
            $params[$key] = self::param($key, $value, $locale);
        }
        $body = __("telegram.events.{$notification->event}", $params, $locale);
        $env = __("telegram.env.{$notification->env}", [], $locale);
        $lines = ["[{$env}] ".(is_string($body) ? $body : $notification->event)];

        if (is_string($notification->link) && $notification->link !== '') {
            $base = config('services.telegram.app_url');
            $url = rtrim(is_string($base) ? $base : 'http://localhost:3000', '/').$notification->link;
            $lines[] = __('telegram.open', ['url' => $url], $locale);
        }

        return implode("\n\n", array_map(static fn (mixed $l): string => is_string($l) ? $l : '', $lines));
    }

    /**
     * Inline Approve / Reject buttons, or null when the notification is not an approval request
     * or the proposal is no longer waiting inside its window.
     *
     * @return array{inline_keyboard: list<list<array{text: string, callback_data: string}>>}|null
     */
    public static function keyboard(Notification $notification, string $locale, ?Carbon $now = null): ?array
    {
        if ($notification->event !== 'proposal_awaiting_approval') {
            return null;
        }
        $id = $notification->params['proposal_id'] ?? null;
        $proposal = is_string($id) ? Proposal::query()->find($id) : null;
        $expires = $proposal?->approval_expires_at;
        if ($proposal === null || $proposal->status !== 'awaiting_owner' || $expires === null || ! $expires->isAfter($now ?? Carbon::now())) {
            return null;
        }
        $unix = $expires->getTimestamp();

        return ['inline_keyboard' => [[
            ['text' => self::str(__('telegram.buttons.approve', [], $locale)), 'callback_data' => CallbackData::encode(CallbackData::APPROVE, $proposal->id, $unix)],
            ['text' => self::str(__('telegram.buttons.reject', [], $locale)), 'callback_data' => CallbackData::encode(CallbackData::REJECT, $proposal->id, $unix)],
        ]]];
    }

    public static function baghdadTime(Carbon $at): string
    {
        return $at->copy()->setTimezone(self::timezone())->format('Y-m-d H:i');
    }

    private static function param(string $key, mixed $value, string $locale): string
    {
        if ($value === null || $value === '') {
            return self::str(__('telegram.none', [], $locale));
        }
        if (is_bool($value)) {
            return self::str(__($value ? 'telegram.yes' : 'telegram.no', [], $locale));
        }
        if (is_array($value)) {
            return implode(', ', array_map(static fn (mixed $v): string => is_scalar($v) ? (string) $v : '', $value));
        }
        if (in_array($key, self::TIME_PARAMS, true) && is_string($value)) {
            try {
                return self::baghdadTime(Carbon::parse($value));
            } catch (\Throwable) {
                return $value;
            }
        }

        return is_scalar($value) ? (string) $value : '';
    }

    private static function timezone(): string
    {
        $tz = config('atlas_defaults.timezone.display');

        return is_string($tz) ? $tz : 'Asia/Baghdad';
    }

    private static function str(mixed $value): string
    {
        return is_string($value) ? $value : '';
    }
}
