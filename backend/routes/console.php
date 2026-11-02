<?php

use App\Support\Backup\BackupSettings;
use Illuminate\Foundation\Inspiring;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\Schedule;

Artisan::command('inspire', function () {
    $this->comment(Inspiring::quote());
})->purpose('Display an inspiring quote');

// ATL-163: lift `/block <instId> today` entries when their Baghdad day ends.
Schedule::command('atlas:expire-blocks')->everyMinute()->withoutOverlapping();

// ATL-231, decision #255 (3): the daily database backup at backup.time, Asia/Baghdad (no DST, so
// it runs exactly once a day). Read on every schedule:run, so a settings change applies with no
// restart. The overlap lock expires after backup.lock_minutes, above pg_dump's own timeout, so a
// killed run never blocks the next day's backup.
$atlasBackupLock = config('atlas_defaults.backup.lock_minutes');
Schedule::command('atlas:backup')
    ->dailyAt(BackupSettings::effective()['time'])
    ->timezone(is_string($tz = config('atlas_defaults.timezone.display')) ? $tz : 'Asia/Baghdad')
    ->when(fn (): bool => BackupSettings::effective()['enabled'])
    ->withoutOverlapping(is_int($atlasBackupLock) ? $atlasBackupLock : 40);
