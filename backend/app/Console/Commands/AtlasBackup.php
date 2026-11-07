<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Models\Backup;
use App\Support\Backup\BackupRunner;
use Illuminate\Console\Attributes\Description;
use Illuminate\Console\Attributes\Signature;
use Illuminate\Console\Command;

/**
 * ATL-231, decision #255 (3): one `pg_dump -Fc` of the Atlas database into backup.folder, keeping
 * the newest backup.keep dumps. Scheduled daily at backup.time Asia/Baghdad (routes/console.php)
 * while backup.enabled is on; runnable by hand at any time. Exits 1 when the backup failed.
 */
#[Signature('atlas:backup')]
#[Description('Back up the Atlas database (pg_dump -Fc) into the configured backup folder')]
class AtlasBackup extends Command
{
    public function handle(BackupRunner $runner): int
    {
        $backup = $runner->run();
        if ($backup === null) {
            $this->warn(__('backup.already_running'));

            return self::SUCCESS;
        }

        if ($backup->status === Backup::OK) {
            $this->info(__('backup.done', ['path' => $backup->path, 'bytes' => (string) $backup->bytes]));

            return self::SUCCESS;
        }

        $this->error(__('backup.failed', ['error' => (string) $backup->error]));

        return self::FAILURE;
    }
}
