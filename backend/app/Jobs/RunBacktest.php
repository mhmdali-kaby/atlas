<?php

declare(strict_types=1);

namespace App\Jobs;

use App\Models\BacktestRun;
use App\Support\TradingCore;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Queue\Queueable;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\Response;
use Illuminate\Support\Carbon;
use RuntimeException;
use Throwable;

/**
 * Runs one owner backtest (ATL-208): calls trading_core's signed, synchronous POST /internal/backtest
 * and stores the body (done) or trading_core's error code and reason (failed).
 *
 * Tries 1: a backtest is never retried silently — every failure is stored with its reason. The job
 * timeout (backtests_api.job_timeout_seconds) sits above the HTTP wait and below the redis queue's
 * retry_after, so a slow run is never handed to a second worker. The endpoint is read-only research;
 * this job touches no order path.
 */
class RunBacktest implements ShouldQueue
{
    use Queueable;

    public int $tries = 1;

    public int $maxExceptions = 1;

    public bool $failOnTimeout = true;

    public int $timeout;

    public function __construct(public readonly string $runId)
    {
        $timeout = config('atlas_defaults.backtests_api.job_timeout_seconds');
        $this->timeout = is_int($timeout) ? $timeout : 80;
        $queue = config('atlas_defaults.backtests_api.queue');
        $this->onQueue(is_string($queue) ? $queue : 'backtests');
    }

    public function handle(): void
    {
        $run = BacktestRun::query()->find($this->runId);
        // Only a queued run starts; a duplicate delivery of a running or finished run does nothing.
        if ($run === null || $run->status !== 'queued') {
            return;
        }
        $run->forceFill(['status' => 'running', 'started_at' => Carbon::now()])->save();

        $httpTimeout = config('atlas_defaults.backtests_api.http_timeout_seconds');
        try {
            $response = TradingCore::fromConfig(is_int($httpTimeout) ? $httpTimeout : 70)
                ->post('/internal/backtest', $run->request);
        } catch (ConnectionException|RuntimeException) {
            $this->fail($run, 'trading_core_unavailable', __('backtests.trading_core_unavailable'));

            return;
        }

        $this->store($run, $response);
    }

    /**
     * The worker killed the job (timeout) or it threw: record it so the run never stays "running".
     */
    public function failed(?Throwable $exception): void
    {
        $run = BacktestRun::query()->find($this->runId);
        if ($run === null || in_array($run->status, ['done', 'failed'], true)) {
            return;
        }
        $this->fail($run, 'job_failed', __('backtests.job_failed'));
    }

    private function store(BacktestRun $run, Response $response): void
    {
        $json = $response->json();
        $body = is_array($json) ? $json : [];

        if ($response->successful() && ($body['ok'] ?? null) === true) {
            $run->forceFill([
                'status' => 'done',
                'result' => $body,
                'error' => null,
                'error_reason' => null,
                'finished_at' => Carbon::now(),
            ])->save();

            return;
        }

        if ($response->status() === 422 && is_string($body['error'] ?? null)) {
            $reason = is_string($body['reason'] ?? null) ? $body['reason'] : $body['error'];
            $this->fail($run, mb_substr($body['error'], 0, 64), $reason);

            return;
        }

        $detail = $body['reason'] ?? $body['detail'] ?? null;
        $reason = is_string($detail) && $detail !== ''
            ? $detail
            : __('backtests.trading_core_error').' (HTTP '.$response->status().')';
        $this->fail($run, 'trading_core_error', $reason);
    }

    private function fail(BacktestRun $run, string $error, string $reason): void
    {
        $run->forceFill([
            'status' => 'failed',
            'result' => null,
            'error' => $error,
            'error_reason' => mb_substr($reason, 0, 2000),
            'finished_at' => Carbon::now(),
        ])->save();
    }
}
