<?php

declare(strict_types=1);

namespace App\Support\TwoFactor;

use App\Models\User;
use BaconQrCode\Renderer\Image\SvgImageBackEnd;
use BaconQrCode\Renderer\ImageRenderer;
use BaconQrCode\Renderer\RendererStyle\RendererStyle;
use BaconQrCode\Writer;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Str;
use PragmaRX\Google2FA\Google2FA;
use SensitiveParameter;

/**
 * Optional TOTP for the owner (ATL-245, decision #265 (1)). Enrolment (/api/me/two-factor) and the
 * login challenge both go through this class, so the replay guard and the single-use recovery
 * codes live in one place.
 *
 * Storage on users: two_factor_secret (encrypted base32), two_factor_recovery_codes (encrypted JSON
 * list of bcrypt hashes), two_factor_confirmed_at (2FA is on only once set), two_factor_last_timestep
 * (the 30 s step of the last accepted code). Nothing here logs a secret or a code.
 */
final class TwoFactor
{
    public function __construct(private readonly Google2FA $google2fa) {}

    public function enabled(User $user): bool
    {
        return $user->two_factor_confirmed_at !== null && $user->two_factor_secret !== null;
    }

    public function recoveryCodesLeft(User $user): int
    {
        return $this->enabled($user) ? count($user->two_factor_recovery_codes ?? []) : 0;
    }

    /**
     * Stores a fresh, unconfirmed secret on the user (replacing any earlier unconfirmed one) and
     * returns it. Callers must refuse this when 2FA is already enabled.
     */
    public function startEnrolment(User $user): string
    {
        $secret = $this->google2fa->generateSecretKey(32);

        $user->forceFill([
            'two_factor_secret' => $secret,
            'two_factor_recovery_codes' => null,
            'two_factor_confirmed_at' => null,
            'two_factor_last_timestep' => null,
        ])->save();

        return $secret;
    }

    /**
     * The otpauth:// URL for $secret rendered as an SVG QR code.
     */
    public function qrCodeSvg(User $user, #[SensitiveParameter] string $secret): string
    {
        $url = $this->google2fa->getQRCodeUrl((string) config('app.name', 'Atlas'), $user->email, $secret);
        $size = config('atlas_defaults.two_factor.qr_size_px');
        $renderer = new ImageRenderer(new RendererStyle(is_int($size) ? $size : 192), new SvgImageBackEnd);

        return (new Writer($renderer))->writeString($url);
    }

    /**
     * Checks a 6-digit code against the user's stored secret (confirmed or pending), accepting
     * `two_factor.window` steps either side of now. A code whose step is not newer than the last
     * accepted one is refused, so the same code never works twice. On success the step is saved.
     */
    public function verifyCode(User $user, #[SensitiveParameter] string $code): bool
    {
        $code = preg_replace('/\s+/', '', $code) ?? '';
        if (preg_match('/^\d{6}$/', $code) !== 1) {
            return false;
        }

        return DB::transaction(function () use ($user, $code): bool {
            /** @var User $locked */
            $locked = User::query()->lockForUpdate()->findOrFail($user->id);
            $secret = $locked->two_factor_secret;
            if ($secret === null) {
                return false;
            }

            $step = $this->google2fa->verifyKeyNewer($secret, $code, $locked->two_factor_last_timestep ?? 0, $this->window());
            if (! is_int($step)) {
                return false;
            }

            $locked->forceFill(['two_factor_last_timestep' => $step])->save();
            $user->setRawAttributes($locked->getAttributes(), true);

            return true;
        });
    }

    /**
     * Uses up one recovery code: true when it matched an unused code, which is then removed.
     */
    public function consumeRecoveryCode(User $user, #[SensitiveParameter] string $code): bool
    {
        $code = strtolower(trim($code));
        if ($code === '') {
            return false;
        }

        return DB::transaction(function () use ($user, $code): bool {
            /** @var User $locked */
            $locked = User::query()->lockForUpdate()->findOrFail($user->id);
            if (! $this->enabled($locked)) {
                return false;
            }

            $hashes = $locked->two_factor_recovery_codes ?? [];
            foreach ($hashes as $index => $hash) {
                if (Hash::check($code, $hash)) {
                    unset($hashes[$index]);
                    $locked->forceFill(['two_factor_recovery_codes' => array_values($hashes)])->save();
                    $user->setRawAttributes($locked->getAttributes(), true);

                    return true;
                }
            }

            return false;
        });
    }

    /**
     * Marks 2FA on and issues the first recovery codes. Call only after verifyCode() passed.
     *
     * @return list<string> the plaintext codes, to show once
     */
    public function confirm(User $user): array
    {
        $codes = $this->newRecoveryCodes();
        $user->forceFill([
            'two_factor_confirmed_at' => now(),
            'two_factor_recovery_codes' => $this->hashAll($codes),
        ])->save();

        return $codes;
    }

    /**
     * Replaces every recovery code.
     *
     * @return list<string> the plaintext codes, to show once
     */
    public function regenerateRecoveryCodes(User $user): array
    {
        $codes = $this->newRecoveryCodes();
        $user->forceFill(['two_factor_recovery_codes' => $this->hashAll($codes)])->save();

        return $codes;
    }

    /** Turns 2FA off and clears all four columns. */
    public function disable(User $user): void
    {
        $user->forceFill([
            'two_factor_secret' => null,
            'two_factor_recovery_codes' => null,
            'two_factor_confirmed_at' => null,
            'two_factor_last_timestep' => null,
        ])->save();
    }

    /**
     * @return list<string> e.g. "k3m9q-7xw2p"
     */
    private function newRecoveryCodes(): array
    {
        $count = config('atlas_defaults.two_factor.recovery_codes');
        $codes = [];
        for ($i = 0; $i < (is_int($count) ? $count : 8); $i++) {
            $codes[] = strtolower(Str::random(5).'-'.Str::random(5));
        }

        return $codes;
    }

    /**
     * @param  list<string>  $codes
     * @return list<string>
     */
    private function hashAll(array $codes): array
    {
        return array_map(static fn (string $code): string => Hash::make($code), $codes);
    }

    private function window(): int
    {
        $window = config('atlas_defaults.two_factor.window');

        return is_int($window) ? $window : 1;
    }
}
