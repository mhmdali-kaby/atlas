<?php

declare(strict_types=1);

namespace App\Http\Controllers\Backtests;

use App\Http\Controllers\Controller;
use App\Http\Requests\Backtests\StoreBacktestRequest;
use App\Jobs\RunBacktest;
use App\Models\BacktestRun;
use App\Models\User;
use App\Support\Auditable;
use App\Support\System\ActiveEnv;
use Illuminate\Database\Eloquent\Builder;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

/**
 * /api/backtests (decision #253 L, ATL-208). The owner starts a backtest (202 + id, run by the queued
 * RunBacktest job on its own Horizon queue), lists recent runs and fetches one run's status and result.
 * Runs belong to the active env and to the owner who started them; anything else is 404.
 */
class BacktestController extends Controller
{
    public function __construct(private readonly Auditable $audit) {}

    public function store(StoreBacktestRequest $request): JsonResponse
    {
        /** @var User $user */
        $user = $request->user();
        $body = $request->backtest();

        $run = DB::transaction(function () use ($user, $body): BacktestRun {
            $run = BacktestRun::query()->create([
                'env' => ActiveEnv::get(),
                'user_id' => $user->id,
                'request' => $body,
                'status' => 'queued',
            ]);

            /** @var array<string, mixed> $rule */
            $rule = $body['rule_spec'];
            $this->audit->record(
                action: 'backtest.started',
                subjectType: 'backtest_runs',
                subjectId: $run->id,
                after: [
                    'env' => $run->env,
                    'rule' => $rule['name'] ?? null,
                    'inst_ids' => $body['inst_ids'],
                    'bar' => $body['bar'],
                    'period' => $body['period'],
                ],
            );

            return $run;
        });

        RunBacktest::dispatch($run->id)->afterCommit();

        return response()->json([
            'ok' => true,
            'id' => $run->id,
            'status' => $run->status,
            'created_at' => $run->created_at->toIso8601ZuluString('microsecond'),
        ], 202);
    }

    public function index(Request $request): JsonResponse
    {
        $limit = config('atlas_defaults.backtests_api.list_limit');

        $runs = $this->scoped($request)
            ->orderByDesc('created_at')
            ->orderByDesc('id')
            ->limit(is_int($limit) ? $limit : 50)
            ->get();

        return response()->json([
            'env' => ActiveEnv::get(),
            'data' => $runs->map(fn (BacktestRun $run): array => $this->summary($run))->values()->all(),
        ]);
    }

    public function show(Request $request, string $backtest): JsonResponse
    {
        $run = $this->scoped($request)->whereKey($backtest)->firstOrFail();

        return response()->json([
            ...$this->summary($run),
            'request' => $run->request,
            'result' => $run->result,
        ]);
    }

    /**
     * @return Builder<BacktestRun>
     */
    private function scoped(Request $request): Builder
    {
        /** @var User $user */
        $user = $request->user();

        return BacktestRun::query()
            ->where('env', ActiveEnv::get())
            ->where('user_id', $user->id);
    }

    /**
     * A run without its heavy result: the list row, and the head of the detail response.
     *
     * @return array<string, mixed>
     */
    private function summary(BacktestRun $run): array
    {
        $request = $run->request;
        /** @var array<string, mixed> $rule */
        $rule = is_array($request['rule_spec'] ?? null) ? $request['rule_spec'] : [];
        $result = $run->result;

        return [
            'id' => $run->id,
            'env' => $run->env,
            'status' => $run->status,
            'rule_name' => is_string($rule['name'] ?? null) ? $rule['name'] : null,
            'side' => is_string($rule['side'] ?? null) ? $rule['side'] : 'long',
            'inst_ids' => $request['inst_ids'] ?? [],
            'bar' => $request['bar'] ?? null,
            'period' => $request['period'] ?? null,
            'n_trades' => $result['n_trades'] ?? null,
            'overfit_warning' => $result['overfit_warning'] ?? null,
            'data_gap' => $result['data_gap'] ?? null,
            'error' => $run->error,
            'error_reason' => $run->error_reason,
            'created_at' => $run->created_at->toIso8601ZuluString('microsecond'),
            'started_at' => $run->started_at?->toIso8601ZuluString('microsecond'),
            'finished_at' => $run->finished_at?->toIso8601ZuluString('microsecond'),
        ];
    }
}
