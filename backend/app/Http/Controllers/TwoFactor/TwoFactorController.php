<?php

declare(strict_types=1);

namespace App\Http\Controllers\TwoFactor;

use App\Http\Controllers\Controller;
use App\Http\Requests\TwoFactor\ConfirmTwoFactorRequest;
use App\Http\Requests\TwoFactor\TwoFactorPasswordRequest;
use App\Models\User;
use App\Support\Auditable;
use App\Support\TwoFactor\TwoFactor;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

/**
 * /api/me/two-factor (ATL-245, decision #265 (1)): optional TOTP enrolment for the signed-in owner.
 *
 * start {password} stores an unconfirmed secret and returns it once with its QR code; confirm {code}
 * turns 2FA on and returns the 8 recovery codes once; recovery-codes {password} replaces them;
 * DELETE {password} clears everything. The secret is never returned after confirm, and no secret or
 * code reaches audit_log or the logs.
 */
class TwoFactorController extends Controller
{
    public function __construct(
        private readonly TwoFactor $twoFactor,
        private readonly Auditable $audit,
    ) {}

    public function status(Request $request): JsonResponse
    {
        return response()->json(['data' => $this->statusPayload($this->owner($request))]);
    }

    public function start(TwoFactorPasswordRequest $request): JsonResponse
    {
        $user = $this->owner($request);
        if ($this->twoFactor->enabled($user)) {
            return $this->conflict('already_enabled');
        }

        $secret = DB::transaction(function () use ($user): string {
            $secret = $this->twoFactor->startEnrolment($user);
            $this->audit->record('two_factor.started', 'users', $user->id, null, ['enabled' => false], $user);

            return $secret;
        });

        return response()->json(['data' => [
            'qr_svg' => $this->twoFactor->qrCodeSvg($user, $secret),
            'secret' => $secret,
        ]]);
    }

    public function confirm(ConfirmTwoFactorRequest $request): JsonResponse
    {
        $user = $this->owner($request);
        if ($this->twoFactor->enabled($user)) {
            return $this->conflict('already_enabled');
        }
        if ($user->two_factor_secret === null) {
            return $this->conflict('not_started');
        }
        if (! $this->twoFactor->verifyCode($user, $request->code())) {
            return response()->json([
                'message' => __('two_factor.code_wrong'),
                'errors' => ['code' => [__('two_factor.code_wrong')]],
            ], 422);
        }

        $codes = DB::transaction(function () use ($user): array {
            $codes = $this->twoFactor->confirm($user);
            $this->audit->record('two_factor.confirmed', 'users', $user->id, ['enabled' => false], [
                'enabled' => true,
                'recovery_codes_left' => count($codes),
            ], $user);

            return $codes;
        });

        return response()->json(['data' => [
            ...$this->statusPayload($user),
            'recovery_codes' => $codes,
        ]]);
    }

    public function regenerate(TwoFactorPasswordRequest $request): JsonResponse
    {
        $user = $this->owner($request);
        if (! $this->twoFactor->enabled($user)) {
            return $this->conflict('not_enabled');
        }

        $before = $this->twoFactor->recoveryCodesLeft($user);
        $codes = DB::transaction(function () use ($user, $before): array {
            $codes = $this->twoFactor->regenerateRecoveryCodes($user);
            $this->audit->record('two_factor.recovery_codes_regenerated', 'users', $user->id,
                ['recovery_codes_left' => $before], ['recovery_codes_left' => count($codes)], $user);

            return $codes;
        });

        return response()->json(['data' => [
            ...$this->statusPayload($user),
            'recovery_codes' => $codes,
        ]]);
    }

    public function disable(TwoFactorPasswordRequest $request): JsonResponse
    {
        $user = $this->owner($request);
        $wasEnabled = $this->twoFactor->enabled($user);

        DB::transaction(function () use ($user, $wasEnabled): void {
            $this->twoFactor->disable($user);
            $this->audit->record('two_factor.disabled', 'users', $user->id,
                ['enabled' => $wasEnabled], ['enabled' => false], $user);
        });

        return response()->json(['data' => $this->statusPayload($user)]);
    }

    private function owner(Request $request): User
    {
        $user = $request->user();
        abort_unless($user instanceof User, 401);

        return $user;
    }

    /**
     * @return array{enabled: bool, confirmed_at: string|null, recovery_codes_left: int}
     */
    private function statusPayload(User $user): array
    {
        $enabled = $this->twoFactor->enabled($user);

        return [
            'enabled' => $enabled,
            'confirmed_at' => $enabled ? $user->two_factor_confirmed_at?->toIso8601ZuluString() : null,
            'recovery_codes_left' => $this->twoFactor->recoveryCodesLeft($user),
        ];
    }

    private function conflict(string $error): JsonResponse
    {
        return response()->json([
            'message' => __("two_factor.{$error}"),
            'error' => $error,
        ], 409);
    }
}
