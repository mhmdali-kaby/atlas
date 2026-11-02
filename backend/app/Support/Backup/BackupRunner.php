<?php

declare(strict_types=1);

namespace App\Support\Backup;

use App\Models\Backup;
use App\Support\Notifications\NotificationMapper;
use App\Support\Notifications\Notifier;
use App\Support\System\ActiveEnv;
use Carbon\CarbonImmutable;
use Illuminate\Process\Exceptions\ProcessFailedException;
use Illuminate\Process\Factory as ProcessFactory;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Str;
use RuntimeException;
use Throwable;

/**
 * One database backup (ATL-231, decision #255 (3)): `pg_dump -Fc` of the Atlas database into the
 * owner's backup.folder as `atlas-<UTC yyyymmdd-HHMMSS>.dump`, then the oldest dumps above
 * backup.keep are deleted (only files named like that), and the run is recorded in `backups`.
 *
 * The password reaches pg_dump only through PGPASSWORD, never argv (argv is visible to every user
 * of the Mac in `ps`). A failed run keeps pg_dump's error text on its row, removes any partial
 * file, and creates a backup_failed notification. One run at a time: the scheduler, the command
 * and the API's job share one cache lock.
 */
final class BackupRunner
{
    public const LOCK = 'atlas:backup:run';

    public function __construct(
        private readonly ProcessFactory $process,
        private readonly Notifier $notifier,
    ) {}

    /** The recorded run, or null when another run holds the lock. */
    public function run(): ?Backup
    {
        $lock = Cache::lock(self::LOCK, self::int('lock_minutes', 40) * 60);
        if (! $lock->get()) {
            return null;
        }
        try {
            return $this->backup();
        } finally {
            $lock->release();
        }
    }

    private function backup(): Backup
    {
        $settings = BackupSettings::effective();
        $started = CarbonImmutable::now('UTC');
        $folder = $settings['folder'];
        $file = rtrim($folder, '/').'/atlas-'.$started->format('Ymd-His').'.dump';
        $wrote = false;

        try {
            if (! is_dir($folder) && ! @mkdir($folder, 0700, true) && ! is_dir($folder)) {
                throw new RuntimeException(__('backup.folder_create_failed', ['folder' => $folder]));
            }
            if (file_exists($file)) {
                throw new RuntimeException(__('backup.file_exists', ['file' => basename($file)]));
            }

            $wrote = true;
            $this->dump($file);

            // The dump holds every table, encrypted keys included: owner-only, like the folder.
            @chmod($file, 0600);
            clearstatcache(true, $file);
            $bytes = is_file($file) ? filesize($file) : false;
            if ($bytes === false || $bytes === 0) {
                throw new RuntimeException(__('backup.empty_dump'));
            }
        } catch (Throwable $e) {
            if ($wrote && is_file($file)) {
                @unlink($file);
            }

            return $this->failed($file, $started, self::errorText($e));
        }

        $backup = Backup::query()->create([
            'path' => $file,
            'bytes' => $bytes,
            'started_at' => $started,
            'finished_at' => CarbonImmutable::now('UTC'),
            'status' => Backup::OK,
            'error' => null,
        ]);
        $this->prune($folder, $settings['keep']);

        return $backup;
    }

    private function dump(string $file): void
    {
        $connection = config('database.connections.'.self::str(config('database.default'), 'pgsql'));
        $db = is_array($connection) ? $connection : [];

        $this->process->newPendingProcess()
            ->timeout(self::int('timeout_seconds', 1800))
            ->env(['PGPASSWORD' => self::str($db['password'] ?? null, '')])
            ->run([
                self::str(config('services.pg_dump.bin'), 'pg_dump'),
                '--format=custom',
                '--no-password',
                '--host='.self::str($db['host'] ?? null, '127.0.0.1'),
                '--port='.self::str((string) ($db['port'] ?? ''), '5432'),
                '--username='.self::str($db['username'] ?? null, 'atlas'),
                '--dbname='.self::str($db['database'] ?? null, 'atlas'),
                '--file='.$file,
            ])
            ->throw();
    }

    /**
     * Deletes the oldest `atlas-<timestamp>.dump` files above $keep. Any other file in the folder
     * (the owner's notes, another tool's dumps) is never touched.
     *
     * @return list<string> the deleted file names
     */
    public function prune(string $folder, int $keep): array
    {
        $entries = @scandir($folder);
        if ($entries === false) {
            return [];
        }
        $dumps = array_values(array_filter(
            $entries,
            fn (string $name): bool => preg_match(BackupSettings::FILE_PATTERN, $name) === 1 && is_file($folder.'/'.$name),
        ));
        // The UTC timestamp in the name sorts in time order.
        rsort($dumps, SORT_STRING);

        $deleted = [];
        foreach (array_slice($dumps, max(1, $keep)) as $name) {
            if (@unlink($folder.'/'.$name)) {
                $deleted[] = $name;
            }
        }

        return $deleted;
    }

    private function failed(string $file, CarbonImmutable $started, string $error): Backup
    {
        $finished = CarbonImmutable::now('UTC');
        $backup = Backup::query()->create([
            'path' => $file,
            'bytes' => null,
            'started_at' => $started,
            'finished_at' => $finished,
            'status' => Backup::FAILED,
            'error' => $error,
        ]);
        Log::error('atlas.backup.failed', ['backup_id' => $backup->id]);

        try {
            $this->notifier->handle('atlas.system', [
                'id' => (string) Str::uuid(),
                'type' => NotificationMapper::BACKUP_FAILED_EVENT,
                'ts' => $finished->format('Y-m-d\TH:i:s.u\Z'),
                'env' => ActiveEnv::get() === 'live' ? 'live' : 'demo',
                'source' => 'service:backend.backup',
                'correlation_id' => null,
                'payload' => [
                    'backup_id' => $backup->id,
                    'started_at' => $started->format('Y-m-d\TH:i:s\Z'),
                ],
            ]);
        } catch (Throwable $e) {
            // The row is the record; a notification failure must not hide it.
            Log::warning('atlas.backup.notify_failed', ['backup_id' => $backup->id, 'exception' => $e::class]);
        }

        return $backup;
    }

    /** pg_dump's own words when it exited non-zero, otherwise the exception's message. */
    private static function errorText(Throwable $e): string
    {
        if ($e instanceof ProcessFailedException) {
            $stderr = trim($e->result->errorOutput());
            $text = __('backup.pg_dump_exit', ['code' => (string) $e->result->exitCode()])
                .($stderr !== '' ? ': '.$stderr : '');
        } else {
            $text = trim($e->getMessage()) !== '' ? trim($e->getMessage()) : $e::class;
        }

        return mb_substr($text, 0, self::int('error_max_length', 2000));
    }

    private static function int(string $key, int $default): int
    {
        $value = config("atlas_defaults.backup.{$key}");

        return is_int($value) ? $value : $default;
    }

    private static function str(mixed $value, string $default): string
    {
        return is_string($value) && $value !== '' ? $value : $default;
    }
}
