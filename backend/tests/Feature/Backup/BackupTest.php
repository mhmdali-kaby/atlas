<?php

declare(strict_types=1);

use App\Jobs\RunBackup;
use App\Models\AuditLog;
use App\Models\Backup;
use App\Models\Notification;
use App\Models\Setting;
use App\Models\User;
use App\Support\Backup\BackupRunner;
use App\Support\Backup\BackupSettings;
use App\Support\Settings\SettingsService;
use Illuminate\Console\Scheduling\Event;
use Illuminate\Console\Scheduling\Schedule;
use Illuminate\Process\PendingProcess;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\File;
use Illuminate\Support\Facades\Process;
use Illuminate\Support\Facades\Queue;
use Illuminate\Support\Facades\RateLimiter;
use Tests\Support\RecordingBroadcaster;

/*
 * ATL-231, decision #255 (3): `php artisan atlas:backup`, its settings, GET/POST /api/backup and the
 * schedule. pg_dump itself is faked here (Process::fake); the real-database check is in the
 * handoff note and self-check.
 */

const ATL231_DUMP = "PGDMP\x01\x0e\x00fake custom-format dump\n";

/** A folder the runner may write to (the settings service does not run the PATCH folder rule). */
function atl231Folder(): string
{
    $dir = storage_path('framework/testing/atl231-'.bin2hex(random_bytes(4)));
    File::ensureDirectoryExists($dir);

    return $dir;
}

/** @param  array<string, mixed>  $changes */
function atl231Settings(array $changes): void
{
    $service = app(SettingsService::class);
    $row = $service->current('demo');
    $service->update('demo', $row->version, $changes);
}

/** pg_dump that writes ATL231_DUMP to its --file= argument, like the real one. */
function atl231FakeDump(): void
{
    Process::fake(function (PendingProcess $process) {
        $command = is_array($process->command) ? $process->command : [];
        foreach ($command as $arg) {
            if (str_starts_with($arg, '--file=')) {
                file_put_contents(substr($arg, 7), ATL231_DUMP);
            }
        }

        return Process::result();
    });
}

/** @return list<string> */
function atl231Files(string $dir): array
{
    $names = array_values(array_diff(scandir($dir) ?: [], ['.', '..']));
    sort($names);

    return $names;
}

beforeEach(function (): void {
    $this->broadcasts = RecordingBroadcaster::install();
    $this->dir = atl231Folder();
    atl231Settings([BackupSettings::FOLDER => $this->dir]);
});

afterEach(function (): void {
    File::deleteDirectory($this->dir);
});

it('writes a non-empty dump with the password only in PGPASSWORD, and records an ok run', function (): void {
    atl231FakeDump();
    // A distinctive password (the test DB's is "atlas", which is also the user name). The open
    // connection is unaffected; only what the runner hands pg_dump changes.
    config(['database.connections.pgsql.password' => 'atl231-test-only-db-password']);

    $code = Artisan::call('atlas:backup');

    expect($code)->toBe(0);
    $files = atl231Files($this->dir);
    expect($files)->toHaveCount(1)
        ->and($files[0])->toMatch(BackupSettings::FILE_PATTERN);

    $row = Backup::query()->sole();
    expect($row->status)->toBe('ok')
        ->and($row->bytes)->toBe(strlen(ATL231_DUMP))
        ->and($row->path)->toBe($this->dir.'/'.$files[0])
        ->and($row->error)->toBeNull()
        ->and($row->finished_at->greaterThanOrEqualTo($row->started_at))->toBeTrue();

    $password = 'atl231-test-only-db-password';
    Process::assertRan(function (PendingProcess $process) use ($password): bool {
        $argv = is_array($process->command) ? $process->command : [];
        foreach ($argv as $arg) {
            if ($password !== '' && str_contains($arg, $password)) {
                return false;
            }
        }

        return $argv[0] === config('services.pg_dump.bin')
            && in_array('--format=custom', $argv, true)
            && in_array('--no-password', $argv, true)
            && in_array('--dbname='.config('database.connections.pgsql.database'), $argv, true)
            && ($process->environment['PGPASSWORD'] ?? null) === $password
            && $process->timeout === config('atlas_defaults.backup.timeout_seconds');
    });
});

it('keeps the newest backup.keep dumps and never deletes any other file', function (): void {
    atl231Settings([BackupSettings::KEEP => 2]);
    foreach (['atlas-20260101-030000.dump', 'atlas-20260102-030000.dump', 'atlas-20260103-030000.dump', 'notes.txt', 'atlas-manual.dump'] as $name) {
        file_put_contents($this->dir.'/'.$name, 'old');
    }
    atl231FakeDump();

    expect(Artisan::call('atlas:backup'))->toBe(0);

    $new = basename(Backup::query()->sole()->path);
    expect(atl231Files($this->dir))->toEqualCanonicalizing([
        'atlas-20260103-030000.dump', $new, 'notes.txt', 'atlas-manual.dump',
    ]);
});

it('records a failed pg_dump with its error, removes the partial file and raises backup_failed', function (): void {
    Process::fake(function (PendingProcess $process) {
        foreach (is_array($process->command) ? $process->command : [] as $arg) {
            if (str_starts_with($arg, '--file=')) {
                file_put_contents(substr($arg, 7), 'partial');
            }
        }

        return Process::result(errorOutput: 'pg_dump: error: server version: 16.4; pg_dump version: 15.2', exitCode: 1);
    });

    $code = Artisan::call('atlas:backup');

    expect($code)->toBe(1)
        ->and(Artisan::output())->toContain('server version: 16.4');
    $row = Backup::query()->sole();
    expect($row->status)->toBe('failed')
        ->and($row->bytes)->toBeNull()
        ->and($row->error)->toContain('pg_dump exited with code 1')
        ->and($row->error)->toContain('server version: 16.4');
    expect(atl231Files($this->dir))->toBe([]);

    $notification = Notification::query()->sole();
    expect($notification->event)->toBe('backup_failed')
        ->and($notification->severity)->toBe('warning')
        ->and($notification->link)->toBe('/settings/backup')
        ->and($notification->params['backup_id'] ?? null)->toBe($row->id)
        // pg_dump's words stay on the backups row, never in a notification.
        ->and(json_encode($notification->params, JSON_THROW_ON_ERROR))->not->toContain('16.4');
    expect(collect($this->broadcasts->sent)->pluck('event')->all())->toContain('notification.created');
});

it('records a failure when pg_dump exits 0 but writes nothing', function (): void {
    Process::fake();

    expect(Artisan::call('atlas:backup'))->toBe(1);
    expect(Backup::query()->sole())
        ->status->toBe('failed')
        ->error->toBe(__('backup.empty_dump'));
});

it('skips a run while another holds the lock', function (): void {
    atl231FakeDump();
    $lock = cache()->lock(BackupRunner::LOCK, 60);
    $lock->get();

    expect(Artisan::call('atlas:backup'))->toBe(0)
        ->and(Artisan::output())->toContain(__('backup.already_running'));
    expect(Backup::query()->count())->toBe(0);
    $lock->release();
});

describe('PATCH /api/settings backup.*', function (): void {
    beforeEach(function (): void {
        $this->owner = User::factory()->create();
        $this->actingAs($this->owner, 'web');
    });

    /** @param  array<string, mixed>  $changes */
    function atl231Patch(array $changes): Illuminate\Testing\TestResponse
    {
        $version = Setting::query()->where('env', 'demo')->value('version');

        return test()->patchJson('/api/settings', ['env' => 'demo', 'version' => $version, 'changes' => $changes]);
    }

    it('refuses a folder that is relative, inside the repository or unwritable, naming the field', function (string $case): void {
        $blocker = tempnam(sys_get_temp_dir(), 'atl231');
        $folder = match ($case) {
            'relative' => 'backups/atlas',
            'dot segments' => sys_get_temp_dir().'/../backups',
            'inside the repository' => BackupSettings::repositoryRoot().'/backups',
            'the repository itself' => BackupSettings::repositoryRoot(),
            'under a file' => $blocker.'/backups',
            'a file' => $blocker,
        };
        $message = match ($case) {
            'relative', 'dot segments' => 'full path',
            'inside the repository', 'the repository itself' => 'outside the Atlas folder',
            default => 'cannot write',
        };

        atl231Patch([BackupSettings::FOLDER => $folder])
            ->assertUnprocessable()
            ->assertJsonValidationErrors(['changes.backup.folder' => $message]);
        @unlink($blocker);
    })->with(['relative', 'dot segments', 'inside the repository', 'the repository itself', 'under a file', 'a file']);

    it('names the field in plain words', function (): void {
        $errors = atl231Patch([BackupSettings::FOLDER => 'relative'])->json('errors');

        expect($errors['changes.backup.folder'][0])->toContain('Backup folder');
    });

    it('accepts a writable folder outside the repository that can be created', function (): void {
        $folder = sys_get_temp_dir().'/atl231-'.bin2hex(random_bytes(4)).'/nested/';

        atl231Patch([BackupSettings::FOLDER => $folder, BackupSettings::TIME => '04:15', BackupSettings::KEEP => 30, BackupSettings::ENABLED => false])
            ->assertOk()
            ->assertJsonPath('data.settings', fn (array $s): bool => $s['backup.folder'] === rtrim($folder, '/')
                && $s['backup.time'] === '04:15' && $s['backup.keep'] === 30 && $s['backup.enabled'] === false);

        expect(BackupSettings::effective())->toBe([
            'enabled' => false, 'folder' => rtrim($folder, '/'), 'time' => '04:15', 'keep' => 30,
        ]);
        // Validation never creates the folder.
        expect(is_dir($folder))->toBeFalse();
    });

    it('refuses a bad time and a keep outside 1–90', function (mixed $time, mixed $keep, string $field): void {
        $changes = $time !== null ? [BackupSettings::TIME => $time] : [BackupSettings::KEEP => $keep];

        atl231Patch($changes)->assertUnprocessable()->assertJsonValidationErrors(["changes.{$field}"]);
    })->with([
        ['24:00', null, 'backup.time'],
        ['3:00', null, 'backup.time'],
        [null, 0, 'backup.keep'],
        [null, 91, 'backup.keep'],
        [null, '5', 'backup.keep'],
    ]);
});

describe('/api/backup', function (): void {
    beforeEach(function (): void {
        $this->owner = User::factory()->create();
        $this->actingAs($this->owner, 'web');
        RateLimiter::clear('backup-run');
    });

    it('needs a signed-in owner', function (): void {
        auth()->guard('web')->logout();
        $this->getJson('/api/backup')->assertUnauthorized();
        $this->postJson('/api/backup/run')->assertUnauthorized();
    });

    it('returns the settings in force, the last 10 runs newest first, and the last good age', function (): void {
        $this->travelTo(now('UTC')->startOfMinute());
        for ($i = 12; $i >= 1; $i--) {
            Backup::query()->create([
                'path' => $this->dir."/atlas-run-{$i}.dump", 'bytes' => $i === 1 ? null : 1000 + $i,
                'started_at' => now()->subHours($i)->subMinute(), 'finished_at' => now()->subHours($i),
                'status' => $i === 1 ? 'failed' : 'ok', 'error' => $i === 1 ? 'pg_dump exited with code 1' : null,
            ]);
        }

        $response = $this->getJson('/api/backup')->assertOk();

        $response->assertJsonPath('data.settings', [
            'enabled' => true, 'folder' => $this->dir, 'time' => '03:00', 'timezone' => 'Asia/Baghdad', 'keep' => 14,
        ]);
        $runs = $response->json('data.runs');
        expect($runs)->toHaveCount(10)
            ->and($runs[0]['status'])->toBe('failed')
            ->and($runs[0]['error'])->toBe('pg_dump exited with code 1')
            ->and($runs[1]['file'])->toBe('atlas-run-2.dump')
            ->and($runs[1]['bytes'])->toBe(1002);
        $response->assertJsonPath('data.last_good.file', 'atlas-run-2.dump')
            ->assertJsonPath('data.last_good.age_seconds', 7200)
            ->assertJsonPath('data.stale_after_hours', 26)
            ->assertJsonPath('data.stale', false);
    });

    it('reports no good backup as stale', function (): void {
        $this->getJson('/api/backup')->assertOk()
            ->assertJsonPath('data.runs', [])
            ->assertJsonPath('data.last_good', null)
            ->assertJsonPath('data.stale', true);
    });

    it('queues one backup now on the backups queue, audits it, and allows one a minute', function (): void {
        Queue::fake();

        $this->postJson('/api/backup/run')->assertStatus(202)->assertJsonPath('data.queued', true);

        Queue::assertPushed(RunBackup::class, fn (RunBackup $job): bool => $job->queue === 'backups'
            && $job->connection === 'redis-backups' && $job->tries === 1);
        expect(AuditLog::query()->where('action', 'backup.run_requested')->sole())
            ->user_id->toBe($this->owner->id);

        $this->postJson('/api/backup/run')->assertTooManyRequests();
        Queue::assertPushed(RunBackup::class, 1);
    });

    it('runs the same backup from the queued job', function (): void {
        atl231FakeDump();

        (new RunBackup)->handle(app(BackupRunner::class));

        expect(Backup::query()->sole()->status)->toBe('ok');
    });
});

it('schedules atlas:backup daily at backup.time Asia/Baghdad, without overlapping, only while enabled', function (): void {
    /** @var Event $event */
    $event = collect(app(Schedule::class)->events())
        ->first(fn (Event $e): bool => str_contains((string) $e->command, 'atlas:backup'));

    expect($event)->not->toBeNull()
        ->and($event->expression)->toBe('0 3 * * *')
        ->and($event->timezone)->toBe('Asia/Baghdad')
        ->and($event->withoutOverlapping)->toBeTrue()
        ->and($event->expiresAt)->toBe(config('atlas_defaults.backup.lock_minutes'))
        ->and($event->filtersPass(app()))->toBeTrue();

    atl231Settings([BackupSettings::ENABLED => false]);
    expect($event->filtersPass(app()))->toBeFalse();
});

it('reads the schedule time from the settings on every boot', function (): void {
    atl231Settings([BackupSettings::TIME => '04:15']);

    // What the next schedule:run (a fresh boot) sees.
    $fresh = new Schedule;
    Illuminate\Support\Facades\Schedule::swap($fresh);
    require base_path('routes/console.php');

    $event = collect($fresh->events())
        ->first(fn (Event $e): bool => str_contains((string) $e->command, 'atlas:backup'));
    expect($event?->expression)->toBe('15 4 * * *');
});
