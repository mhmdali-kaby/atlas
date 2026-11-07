<?php

declare(strict_types=1);

namespace App\Http\Controllers\Backup;

use App\Http\Controllers\Controller;
use App\Jobs\RunBackup;
use App\Models\Backup;
use App\Models\User;
use App\Support\Auditable;
use App\Support\Backup\BackupSettings;
use App\Support\System\ActiveEnv;
use Carbon\CarbonImmutable;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * /api/backup (ATL-231, decision #255 (3)): the backup settings in force (the active env's row),
 * the last runs, and how old the newest good backup is; POST /run queues one backup now.
 */
class BackupController extends Controller
{
    public function __construct(private readonly Auditable $audit) {}

    public function show(): JsonResponse
    {
        $limit = config('atlas_defaults.backup.runs_listed');
        $staleHours = config('atlas_defaults.backup.stale_after_hours');
        $settings = BackupSettings::effective();
        $now = CarbonImmutable::now('UTC');

        $runs = Backup::query()->orderByDesc('started_at')->orderByDesc('id')
            ->limit(is_int($limit) ? $limit : 10)->get();
        $lastGood = Backup::query()->where('status', Backup::OK)->orderByDesc('finished_at')->first();
        $age = $lastGood !== null ? max(0, (int) $lastGood->finished_at->diffInSeconds($now, true)) : null;
        $stale = is_int($staleHours) ? $staleHours : 26;

        return response()->json(['data' => [
            'env' => ActiveEnv::get(),
            'settings' => [
                'enabled' => $settings['enabled'],
                'folder' => $settings['folder'],
                'time' => $settings['time'],
                'timezone' => self::timezone(),
                'keep' => $settings['keep'],
            ],
            'runs' => $runs->map(fn (Backup $b): array => self::item($b))->all(),
            'last_good' => $lastGood === null ? null : [
                ...self::item($lastGood),
                'age_seconds' => $age,
            ],
            'stale_after_hours' => $stale,
            'stale' => $age === null || $age > $stale * 3600,
        ]]);
    }

    public function run(Request $request): JsonResponse
    {
        $user = $request->user();
        abort_unless($user instanceof User, 401);

        RunBackup::dispatch();
        $this->audit->record('backup.run_requested', 'backups', null, null, [
            'folder' => BackupSettings::effective()['folder'],
        ], $user);

        return response()->json(['data' => ['queued' => true, 'message' => __('backup.queued')]], 202);
    }

    /**
     * @return array{id: string, file: string, path: string, bytes: int|null, status: string, error: string|null, started_at: string, finished_at: string}
     */
    private static function item(Backup $b): array
    {
        return [
            'id' => $b->id,
            'file' => basename($b->path),
            'path' => $b->path,
            'bytes' => $b->bytes,
            'status' => $b->status,
            'error' => $b->error,
            'started_at' => $b->started_at->utc()->format('Y-m-d\TH:i:s.u\Z'),
            'finished_at' => $b->finished_at->utc()->format('Y-m-d\TH:i:s.u\Z'),
        ];
    }

    private static function timezone(): string
    {
        $tz = config('atlas_defaults.timezone.display');

        return is_string($tz) ? $tz : 'Asia/Baghdad';
    }
}
