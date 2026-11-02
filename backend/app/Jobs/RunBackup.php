<?php

declare(strict_types=1);

namespace App\Jobs;

use App\Support\Backup\BackupRunner;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Queue\Queueable;

/**
 * ATL-231: the owner's "Back up now" (POST /api/backup/run). Runs the same BackupRunner as
 * `php artisan atlas:backup`, so it records its run, prunes and notifies the same way. Tries 1:
 * a failure is recorded on the backups row, never retried silently. Runs on its own connection
 * and supervisor (backup.queue_connection / backup.queue), whose retry_after is above this timeout.
 */
class RunBackup implements ShouldQueue
{
    use Queueable;

    public int $tries = 1;

    public int $maxExceptions = 1;

    public bool $failOnTimeout = true;

    public int $timeout;

    public function __construct()
    {
        $timeout = config('atlas_defaults.backup.job_timeout_seconds');
        $this->timeout = is_int($timeout) ? $timeout : 1860;
        $connection = config('atlas_defaults.backup.queue_connection');
        $queue = config('atlas_defaults.backup.queue');
        $this->onConnection(is_string($connection) ? $connection : 'redis-backups');
        $this->onQueue(is_string($queue) ? $queue : 'backups');
    }

    public function handle(BackupRunner $runner): void
    {
        $runner->run();
    }
}
