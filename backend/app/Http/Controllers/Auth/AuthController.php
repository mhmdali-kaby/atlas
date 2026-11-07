<?php

declare(strict_types=1);

namespace App\Http\Controllers\Auth;

use App\Http\Controllers\Controller;
use App\Http\Requests\Auth\LoginRequest;
use App\Http\Requests\Auth\TwoFactorChallengeRequest;
use App\Http\Resources\UserResource;
use App\Models\User;
use App\Support\Auditable;
use App\Support\Notifications\NotificationMapper;
use App\Support\Notifications\Notifier;
use App\Support\System\ActiveEnv;
use App\Support\TwoFactor\TwoFactor;
use Illuminate\Http\Exceptions\ThrottleRequestsException;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\Auth;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\Str;
use Illuminate\Validation\ValidationException;
use Throwable;

/**
 * Sanctum SPA (cookie session) authentication for the single owner. No API tokens.
 *
 * ATL-246, decision #265 (1): when the owner has two-factor authentication on, a correct password
 * does not sign in. It returns {two_factor: true} and leaves a pending challenge (user id + expiry)
 * in the session; POST /two-factor-challenge with a code or recovery code completes the sign-in.
 *
 * ATL-261 (security round 20): throttleChallenge() used to key its 5/min limiter on the session id,
 * so a fresh password login (which regenerates the session) reset the count — 20 wrong codes a
 * minute against a designed 5. The limiter now keys on SUBJECT_KEY, a session value that survives
 * a pending challenge being pulled, set at the same time as the pending challenge and cleared only
 * on a successful challenge. A longer per-user cap (two_factor.challenge_failures_per_window) on top
 * counts wrong codes only and, once tripped, refuses every pending challenge for that user — even a
 * correct code — until the window clears, and raises an auth_two_factor_failures notification. A
 * session that reaches the per-minute limit's own wrong codes also has its pending challenge
 * pulled, so the owner must enter the password again.
 */
class AuthController extends Controller
{
    /**
     * Session key holding the pending challenge: {user_id: string, expires_at: int (unix),
     * wrong_count: int}.
     */
    private const PENDING_KEY = 'auth.two_factor_pending';

    /**
     * The user id a pending challenge was issued for, set alongside PENDING_KEY and kept even once
     * PENDING_KEY is pulled, so the per-minute throttle stays keyed on the same identity for the
     * rest of that session. Cleared only when the challenge is actually completed.
     */
    private const SUBJECT_KEY = 'auth.two_factor_challenge_subject';

    public function __construct(
        private readonly TwoFactor $twoFactor,
        private readonly Auditable $audit,
        private readonly Notifier $notifier,
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
                'wrong_count' => 0,
            ]);
            $request->session()->put(self::SUBJECT_KEY, $user->id);

            return response()->json(['two_factor' => true]);
        }

        $request->session()->forget(self::PENDING_KEY);
        $request->session()->forget(self::SUBJECT_KEY);
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
            $this->recordWrongAttempt($request, $user);

            return response()->json([
                'message' => __('two_factor.challenge_code_wrong'),
                'error' => 'code_wrong',
                'errors' => [$method => [__('two_factor.challenge_code_wrong')]],
            ], 422);
        }

        $request->session()->forget(self::PENDING_KEY);
        $request->session()->forget(self::SUBJECT_KEY);
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
     * ATL-261: 5 attempts per minute (atlas_defaults.two_factor.challenge_attempts_per_minute),
     * keyed on the pending challenge's subject user id (SUBJECT_KEY), or the client IP when there
     * is none — never the session id, which a fresh password login regenerates and so used to
     * reset this count for free. On top, a per-user cap on wrong codes only
     * (two_factor.challenge_failures_per_window within challenge_failure_window_minutes) refuses
     * every pending challenge for that user, correct code included, once it trips (see
     * recordWrongAttempt()). Either cap throws 429 with Retry-After, like the throttle middleware.
     */
    private function throttleChallenge(Request $request): void
    {
        $userId = $this->subjectUserId($request);

        if ($userId !== null) {
            $longKey = $this->failureCapKey($userId);
            if (RateLimiter::tooManyAttempts($longKey, $this->failureCapMax())) {
                $this->throwThrottled($longKey, $this->failureCapMax());
            }
        }

        $identity = $userId ?? ('ip:'.($request->ip() ?? ''));
        $key = 'two-factor-challenge|'.sha1($identity);
        $max = $this->perMinuteMax();

        if (RateLimiter::tooManyAttempts($key, $max)) {
            $this->throwThrottled($key, $max);
        }
        RateLimiter::hit($key, 60);
    }

    /**
     * ATL-261: called once a submitted code or recovery code turned out wrong.
     *
     * Session-side: a 5th wrong code in this session (the same count as the per-minute cap) pulls
     * the pending challenge, so the session cannot simply wait out the minute and keep trying —
     * the owner has to enter the password again.
     *
     * User-side: counts this failure against the longer per-user cap and, the moment it trips,
     * raises an auth_two_factor_failures notification (once, not on every failure after).
     */
    private function recordWrongAttempt(Request $request, User $user): void
    {
        $pending = $request->session()->get(self::PENDING_KEY);
        if (is_array($pending)) {
            $wrong = (is_int($pending['wrong_count'] ?? null) ? $pending['wrong_count'] : 0) + 1;
            if ($wrong >= $this->perMinuteMax()) {
                $request->session()->forget(self::PENDING_KEY);
            } else {
                $pending['wrong_count'] = $wrong;
                $request->session()->put(self::PENDING_KEY, $pending);
            }
        }

        $longKey = $this->failureCapKey($user->id);
        $before = RateLimiter::attempts($longKey);
        RateLimiter::hit($longKey, $this->failureCapWindowSeconds());
        if ($before + 1 === $this->failureCapMax()) {
            $this->raiseTwoFactorFailuresNotification($user);
        }
    }

    private function raiseTwoFactorFailuresNotification(User $user): void
    {
        try {
            $this->notifier->handle('atlas.system', [
                'id' => (string) Str::uuid(),
                'type' => NotificationMapper::AUTH_TWO_FACTOR_FAILURES_EVENT,
                'ts' => now('UTC')->format('Y-m-d\TH:i:s.u\Z'),
                'env' => ActiveEnv::get() === 'live' ? 'live' : 'demo',
                'source' => 'service:backend.auth',
                'correlation_id' => null,
                'payload' => ['user_id' => $user->id],
            ]);
        } catch (Throwable $e) {
            // The rate limiter already refuses the challenge; a notification failure must not
            // undo that.
            Log::warning('atlas.auth.two_factor_failures_notify_failed', ['exception' => $e::class]);
        }
    }

    private function throwThrottled(string $key, int $limit): never
    {
        $retryAfter = RateLimiter::availableIn($key);

        throw new ThrottleRequestsException(headers: [
            'Retry-After' => $retryAfter,
            'X-RateLimit-Limit' => $limit,
            'X-RateLimit-Remaining' => 0,
        ]);
    }

    /**
     * The user id a live or just-pulled pending challenge in this session was issued for, or null
     * when this session never had one — set alongside PENDING_KEY, but (unlike it) not cleared when
     * a wrong code pulls the challenge, so the per-minute throttle stays on the same identity for
     * the rest of the session.
     */
    private function subjectUserId(Request $request): ?string
    {
        if (! $request->hasSession()) {
            return null;
        }
        $subject = $request->session()->get(self::SUBJECT_KEY);

        return is_string($subject) && $subject !== '' ? $subject : null;
    }

    private function perMinuteMax(): int
    {
        $perMinute = config('atlas_defaults.two_factor.challenge_attempts_per_minute');

        return is_int($perMinute) ? $perMinute : 5;
    }

    private function failureCapMax(): int
    {
        $max = config('atlas_defaults.two_factor.challenge_failures_per_window');

        return is_int($max) ? $max : 10;
    }

    private function failureCapWindowSeconds(): int
    {
        $minutes = config('atlas_defaults.two_factor.challenge_failure_window_minutes');

        return (is_int($minutes) ? $minutes : 15) * 60;
    }

    private function failureCapKey(string $userId): string
    {
        return 'two-factor-challenge-failures|'.sha1($userId);
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
