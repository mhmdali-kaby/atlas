<?php

declare(strict_types=1);

namespace App\Support\Telegram;

use Illuminate\Support\Str;

/**
 * Signed callback_data for the Approve / Reject buttons (ATL-215, decision #254 (3)):
 *
 *     <a|r>:<proposal_id>:<expires_unix>:<hmac>
 *
 * Telegram allows 64 bytes, and a dashed UUID plus a hex HMAC-SHA256 would not fit, so two parts
 * are shortened:
 * - proposal_id is the UUID's 16 bytes in unpadded base64url (22 chars);
 * - hmac is the first `telegram.callback_mac_bytes` (18) bytes of
 *   HMAC-SHA256(key, "<a|r>:<proposal_id as sent>:<expires_unix>") in unpadded base64url (24 chars).
 * The key is HMAC-SHA256(APP_KEY bytes, "atlas.telegram.callback.v1"), so it is not APP_KEY itself.
 * Total: 1 + 1 + 22 + 1 + 10 + 1 + 24 = 60 bytes.
 */
final class CallbackData
{
    public const APPROVE = 'a';

    public const REJECT = 'r';

    public const MAX_BYTES = 64;

    private const KEY_LABEL = 'atlas.telegram.callback.v1';

    public static function encode(string $action, string $proposalId, int $expiresUnix): string
    {
        if (! in_array($action, [self::APPROVE, self::REJECT], true) || ! Str::isUuid($proposalId)) {
            throw new \InvalidArgumentException('Bad callback action or proposal id.');
        }
        $body = $action.':'.self::b64(self::uuidBytes($proposalId)).':'.$expiresUnix;

        return $body.':'.self::mac($body);
    }

    /**
     * The verified parts, or null for anything malformed or with a wrong HMAC. Expiry is the
     * caller's check (it needs the clock).
     *
     * @return array{action: 'a'|'r', proposal_id: string, expires_unix: int}|null
     */
    public static function decode(string $data): ?array
    {
        if (strlen($data) > self::MAX_BYTES) {
            return null;
        }
        $parts = explode(':', $data);
        if (count($parts) !== 4) {
            return null;
        }
        [$action, $id, $expires, $mac] = $parts;
        if (($action !== self::APPROVE && $action !== self::REJECT) || preg_match('/^\d{1,12}$/', $expires) !== 1) {
            return null;
        }
        if (! hash_equals(self::mac("{$action}:{$id}:{$expires}"), $mac)) {
            return null;
        }
        $bytes = self::unb64($id);
        if ($bytes === null || strlen($bytes) !== 16) {
            return null;
        }
        $hex = bin2hex($bytes);
        $uuid = substr($hex, 0, 8).'-'.substr($hex, 8, 4).'-'.substr($hex, 12, 4).'-'.substr($hex, 16, 4).'-'.substr($hex, 20);

        return ['action' => $action, 'proposal_id' => $uuid, 'expires_unix' => (int) $expires];
    }

    private static function mac(string $body): string
    {
        $bytes = config('atlas_defaults.telegram.callback_mac_bytes');

        return self::b64(substr(hash_hmac('sha256', $body, self::key(), true), 0, is_int($bytes) ? $bytes : 18));
    }

    private static function key(): string
    {
        $appKey = config('app.key');
        if (! is_string($appKey) || $appKey === '') {
            throw new \RuntimeException('APP_KEY is not set; Telegram buttons cannot be signed.');
        }
        if (str_starts_with($appKey, 'base64:')) {
            $decoded = base64_decode(substr($appKey, 7), true);
            $appKey = $decoded === false ? $appKey : $decoded;
        }

        return hash_hmac('sha256', self::KEY_LABEL, $appKey, true);
    }

    private static function uuidBytes(string $uuid): string
    {
        $bin = hex2bin(str_replace('-', '', strtolower($uuid)));

        return $bin === false ? '' : $bin;
    }

    private static function b64(string $bytes): string
    {
        return rtrim(strtr(base64_encode($bytes), '+/', '-_'), '=');
    }

    private static function unb64(string $text): ?string
    {
        if (preg_match('/^[A-Za-z0-9_-]+$/', $text) !== 1) {
            return null;
        }
        $decoded = base64_decode(strtr($text, '-_', '+/'), true);

        return $decoded === false ? null : $decoded;
    }
}
