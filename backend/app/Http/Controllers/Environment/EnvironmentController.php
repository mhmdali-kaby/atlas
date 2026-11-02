<?php

declare(strict_types=1);

namespace App\Http\Controllers\Environment;

use App\Http\Controllers\Controller;
use App\Http\Requests\Environment\SwitchEnvironmentRequest;
use App\Http\Requests\Environment\UpdateAttestationsRequest;
use App\Models\SystemState;
use App\Models\User;
use App\Support\Auditable;
use App\Support\Events\EventBus;
use App\Support\System\ActiveEnv;
use App\Support\System\Preflight;
use Carbon\CarbonImmutable;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Str;
use Throwable;

/**
 * /api/environment (ATL-236, decision #255 (4)(5)).
 *
 * GET returns the active env, the first live month and the eight pre-flight items. PUT attestations
 * ticks or clears the owner's three items. POST switch moves the active env: to live it needs the
 * owner password, the exact text "TRADE LIVE" and every pre-flight item ok; to demo it needs
 * confirm: true. A switch pauses trading in the new env, writes one audit_log row and publishes
 * `system.env_changed` {from, to} on atlas.system, where trading_core and the agents pick it up.
 */
class EnvironmentController extends Controller
{
    public const EVENT_STREAM = 'atlas.system';

    public const EVENT_TYPE = 'system.env_changed';

    public const EVENT_SOURCE = 'api:environment';

    public function __construct(
        private readonly Auditable $audit,
        private readonly EventBus $events,
        private readonly Preflight $preflight,
    ) {}

    public function show(): JsonResponse
    {
        return response()->json(['data' => $this->payload(CarbonImmutable::now())]);
    }

    public function attestations(UpdateAttestationsRequest $request): JsonResponse
    {
        $changes = $request->changes();
        $now = CarbonImmutable::now();

        DB::transaction(function () use ($changes, $now): void {
            $state = $this->lockedState();
            $current = $state->preflight_attestations;
            $before = [];
            $after = [];
            foreach ($changes as $key => $confirmed) {
                $old = isset($current[$key]['confirmed_at']) ? $current[$key]['confirmed_at'] : null;
                if ($confirmed && $old === null) {
                    $current[$key] = ['confirmed_at' => self::iso($now)];
                } elseif (! $confirmed && $old !== null) {
                    unset($current[$key]);
                } else {
                    continue;
                }
                $before[$key] = $old;
                $after[$key] = $current[$key]['confirmed_at'] ?? null;
            }
            if ($after === []) {
                return;
            }
            $state->preflight_attestations = $current;
            $state->save();
            $this->audit->record('environment.attestations_updated', 'system_state', (string) SystemState::ID, $before, $after);
        });

        return response()->json(['data' => $this->payload($now)]);
    }

    public function switch(SwitchEnvironmentRequest $request): JsonResponse
    {
        $target = $request->target();
        $from = ActiveEnv::get();
        if ($target === $from) {
            return response()->json([
                'message' => __('environment.switch.already', ['env' => __("environment.envs.{$target}")]),
                'error' => 'already_active',
            ], 409);
        }

        $now = CarbonImmutable::now();
        if ($target === 'live') {
            $refused = $this->refuseLive($request, $now);
            if ($refused !== null) {
                return $refused;
            }
        }

        $switched = DB::transaction(function () use ($target, $now): ?string {
            $state = $this->lockedState();
            $from = $state->active_env;
            if ($from === $target) {
                return null; // a concurrent request switched first
            }
            $state->active_env = $target;
            $state->switched_at = $now;
            if ($target === 'live' && $state->live_started_at === null) {
                $state->live_started_at = $now;
            }
            $state->save();
            $this->pauseTrading($target, $now);
            $this->audit->record('environment.switched', 'system_state', (string) SystemState::ID, ['active_env' => $from], [
                'active_env' => $target,
                'live_started_at' => $state->live_started_at !== null ? self::iso(CarbonImmutable::instance($state->live_started_at)) : null,
                'trading_paused_env' => $target,
            ]);

            return $from;
        });

        if ($switched === null) {
            return response()->json([
                'message' => __('environment.switch.already', ['env' => __("environment.envs.{$target}")]),
                'error' => 'already_active',
            ], 409);
        }

        $this->publish($switched, $target, $now);

        return response()->json(['data' => $this->payload($now)]);
    }

    /**
     * The 422 for a refused switch to live, or null when every check passes. A wrong password is
     * reported alone, so a guess never learns the confirmation or pre-flight state.
     */
    private function refuseLive(SwitchEnvironmentRequest $request, CarbonImmutable $now): ?JsonResponse
    {
        $user = $request->user();
        abort_unless($user instanceof User, 401);
        if (! Hash::check($request->string('password')->toString(), $user->password)) {
            return $this->unprocessable(['password' => [(string) __('environment.switch.password')]], []);
        }

        $errors = [];
        $expected = config('atlas_defaults.env.live_preflight.confirmation_text');
        if ($request->string('confirmation')->toString() !== (is_string($expected) ? $expected : 'TRADE LIVE')) {
            $errors['confirmation'] = [(string) __('environment.switch.confirmation', ['text' => is_string($expected) ? $expected : 'TRADE LIVE'])];
        }

        $items = $this->preflight->items($this->state(), $now);
        $failing = Preflight::failing($items);
        if ($failing !== []) {
            $errors['preflight'] = array_map(
                static fn (string $key): string => (string) __('environment.switch.preflight_item', ['item' => __("environment.items.{$key}")]),
                $failing,
            );
        }

        return $errors === [] ? null : $this->unprocessable($errors, $failing);
    }

    /**
     * @param  array<string, list<string>>  $errors
     * @param  list<string>  $failing
     */
    private function unprocessable(array $errors, array $failing): JsonResponse
    {
        $first = array_values($errors)[0][0];

        return response()->json([
            'message' => $first,
            'errors' => $errors,
            'failing_items' => $failing,
        ], 422);
    }

    /** Pauses new orders in $env: trading_state.paused, created when the env has no row yet. */
    private function pauseTrading(string $env, CarbonImmutable $now): void
    {
        $reason = (string) __('environment.switch.pause_reason');
        $updated = DB::table('trading_state')->where('env', $env)->update([
            'paused' => true, 'reason' => $reason, 'paused_at' => $now, 'updated_at' => $now,
        ]);
        if ($updated === 0) {
            DB::table('trading_state')->insert([
                'id' => (string) Str::uuid(), 'env' => $env, 'paused' => true, 'reason' => $reason,
                'breakers' => '{}', 'paused_at' => $now, 'created_at' => $now, 'updated_at' => $now,
            ]);
        }
    }

    /**
     * Postgres is the source of truth; a Redis outage must not undo a committed switch, so it is
     * reported. trading_core and the agents also read system_state on start.
     */
    private function publish(string $from, string $to, CarbonImmutable $now): void
    {
        try {
            $this->events->publish(self::EVENT_STREAM, self::EVENT_TYPE, $to, self::EVENT_SOURCE, [
                'from' => $from,
                'to' => $to,
                'at' => self::iso($now),
            ]);
            $this->events->publish(self::EVENT_STREAM, 'system.trading_state', $to, self::EVENT_SOURCE, [
                'paused' => true,
                'reason' => (string) __('environment.switch.pause_reason'),
                'paused_at' => self::iso($now),
                'by' => 'environment_switch',
                'active_breakers' => [],
                'breakers_cleared' => [],
            ]);
        } catch (Throwable $e) {
            report($e);
        }
    }

    /**
     * @return array<string, mixed>
     */
    private function payload(CarbonImmutable $now): array
    {
        $state = $this->state();
        $liveStarted = $state?->live_started_at !== null ? CarbonImmutable::instance($state->live_started_at) : null;
        $days = config('atlas_defaults.env.live_preflight.first_month_days');
        $firstMonthEnds = $liveStarted?->addDays(is_int($days) ? $days : 30);
        $items = $this->preflight->items($state, $now);

        return [
            'active_env' => ActiveEnv::get(),
            'live_started_at' => $liveStarted !== null ? self::iso($liveStarted) : null,
            'first_month_ends_at' => $firstMonthEnds !== null ? self::iso($firstMonthEnds) : null,
            'in_first_live_month' => $firstMonthEnds !== null && $now->lessThan($firstMonthEnds),
            'switched_at' => $state?->switched_at !== null ? self::iso(CarbonImmutable::instance($state->switched_at)) : null,
            'confirmation_text' => config('atlas_defaults.env.live_preflight.confirmation_text'),
            'preflight' => [
                'ok' => Preflight::failing($items) === [],
                'items' => $items,
            ],
        ];
    }

    private function state(): ?SystemState
    {
        return SystemState::query()->find(SystemState::ID);
    }

    /** The single row, created from the current env when missing, locked for this transaction. */
    private function lockedState(): SystemState
    {
        DB::table('system_state')->insertOrIgnore([
            'id' => SystemState::ID,
            'active_env' => ActiveEnv::get(),
            'preflight_attestations' => '{}',
            'created_at' => CarbonImmutable::now(),
            'updated_at' => CarbonImmutable::now(),
        ]);

        return SystemState::query()->lockForUpdate()->findOrFail(SystemState::ID);
    }

    private static function iso(CarbonImmutable $at): string
    {
        return $at->utc()->format('Y-m-d\TH:i:s.u\Z');
    }
}
