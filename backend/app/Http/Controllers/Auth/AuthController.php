<?php

declare(strict_types=1);

namespace App\Http\Controllers\Auth;

use App\Http\Controllers\Controller;
use App\Http\Requests\Auth\LoginRequest;
use App\Http\Requests\Auth\TwoFactorChallengeRequest;
use App\Http\Resources\UserResource;
use App\Models\User;
use App\Support\Auditable;
use App\Support\TwoFactor\TwoFactor;
use Illuminate\Http\Exceptions\ThrottleRequestsException;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\Auth;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Validation\ValidationException;

/**
 * Sanctum SPA (cookie session) authentication for the single owner. No API tokens.
 *
 * ATL-246, decision #265 (1): when the owner has two-factor authentication on, a correct password
 * does not sign in. It returns {two_factor: true} and leaves a pending challenge (user id + expiry)
 * in the session; POST /two-factor-challenge with a code or recovery code completes the sign-in.
 */
class AuthController extends Controller
{
    /** Session key holding the pending challenge: {user_id: string, expires_at: int (unix)}. */
    private const PENDING_KEY = 'auth.two_factor_pending';

    public function __construct(
        private readonly TwoFactor $twoFactor,
        private readonly Auditable $audit,
    ) {}

    /**
     * Unauthenticated: says only whether an owner account exists, nothing about who it is, and
     * whether this session is waiting for its sign-in code (so the SPA can resume that step).
     */
    public function status(Request $request): JsonResponse
    {
        return response()->json([
            'owner_exists' => User::query()->exists(),
            'two_factor_pending' => $this->pendingUser($request) !== null,
        ]);
    }

    public function login(LoginRequest $request): UserResource|JsonResponse
    {
        /** @var array{email: string, password: string} $credentials */
        $credentials = $request->validated();
        $guard = Auth::guard('web');

        if (! $guard->validate($credentials)) {
            throw ValidationException::withMessages([
                'email' => [__('auth.failed')],
            ]);
        }

        $user = $guard->getProvider()->retrieveByCredentials($credentials);
        if (! $user instanceof User) {
            throw ValidationException::withMessages([
                'email' => [__('auth.failed')],
            ]);
        }

        if ($this->twoFactor->enabled($user)) {
            $request->session()->regenerate();
            $minutes = config('atlas_defaults.two_factor.challenge_minutes');
            $request->session()->put(self::PENDING_KEY, [
                'user_id' => $user->id,
                'expires_at' => now()->addMinutes(is_int($minutes) ? $minutes : 5)->getTimestamp(),
            ]);

            return response()->json(['two_factor' => true]);
        }

        $request->session()->forget(self::PENDING_KEY);
        $guard->login($user);
        $request->session()->regenerate();

        return new UserResource($user);
    }

    /**
     * The second sign-in step: {code} from the authenticator app, or {recovery_code} (single use).
     * 422 errors.code when wrong; 422 error=challenge_expired when there is no live pending challenge.
     */
    public function twoFactorChallenge(TwoFactorChallengeRequest $request): UserResource|JsonResponse
    {
        $this->throttleChallenge($request);

        $user = $this->pendingUser($request);
        $code = $request->code();
        $method = $code !== null ? 'code' : 'recovery_code';

        if ($user === null || ! $this->twoFactor->enabled($user)) {
            $pending = $request->session()->pull(self::PENDING_KEY);
            if (is_array($pending) && is_string($pending['user_id'] ?? null)) {
                $this->audit->record('auth.two_factor_challenge.failed', 'users', $pending['user_id'], null,
                    ['method' => $method, 'reason' => 'expired']);
            }

            return response()->json([
                'message' => __('two_factor.challenge_expired'),
                'error' => 'challenge_expired',
                'errors' => [$method => [__('two_factor.challenge_expired')]],
            ], 422);
        }

        $passed = $code !== null
            ? $this->twoFactor->verifyCode($user, $code)
            : $this->twoFactor->consumeRecoveryCode($user, $request->recoveryCode());

        if (! $passed) {
            $this->audit->record('auth.two_factor_challenge.failed', 'users', $user->id, null,
                ['method' => $method, 'reason' => 'wrong_code'], $user);

            return response()->json([
                'message' => __('two_factor.challenge_code_wrong'),
                'error' => 'code_wrong',
                'errors' => [$method => [__('two_factor.challenge_code_wrong')]],
            ], 422);
        }

        $request->session()->forget(self::PENDING_KEY);
        Auth::guard('web')->login($user);
        $request->session()->regenerate();

        $after = ['method' => $method];
        if ($method === 'recovery_code') {
            $after['recovery_codes_left'] = $this->twoFactor->recoveryCodesLeft($user);
        }
        $this->audit->record('auth.two_factor_challenge.passed', 'users', $user->id, null, $after, $user);

        return new UserResource($user);
    }

    public function logout(Request $request): Response
    {
        Auth::guard('web')->logout();

        $request->session()->invalidate();
        $request->session()->regenerateToken();

        return response()->noContent();
    }

    public function me(Request $request): UserResource
    {
        /** @var User $user */
        $user = $request->user();

        return new UserResource($user);
    }

    /**
     * 5 attempts per minute (atlas_defaults.two_factor.challenge_attempts_per_minute) per session
     * id + client IP; the next one is 429 with Retry-After, like the throttle middleware.
     */
    private function throttleChallenge(Request $request): void
    {
        $perMinute = config('atlas_defaults.two_factor.challenge_attempts_per_minute');
        $max = is_int($perMinute) ? $perMinute : 5;
        $key = 'two-factor-challenge|'.sha1($request->session()->getId().'|'.($request->ip() ?? ''));

        if (RateLimiter::tooManyAttempts($key, $max)) {
            $retryAfter = RateLimiter::availableIn($key);

            throw new ThrottleRequestsException(headers: [
                'Retry-After' => $retryAfter,
                'X-RateLimit-Limit' => $max,
                'X-RateLimit-Remaining' => 0,
            ]);
        }
        RateLimiter::hit($key, 60);
    }

    /** The user this session's live (unexpired) pending challenge is for, or null. */
    private function pendingUser(Request $request): ?User
    {
        if (! $request->hasSession()) {
            return null;
        }
        $pending = $request->session()->get(self::PENDING_KEY);
        if (! is_array($pending) || ! is_string($pending['user_id'] ?? null) || ! is_int($pending['expires_at'] ?? null)) {
            return null;
        }
        if ($pending['expires_at'] <= now()->getTimestamp()) {
            return null;
        }

        return User::query()->find($pending['user_id']);
    }
}
