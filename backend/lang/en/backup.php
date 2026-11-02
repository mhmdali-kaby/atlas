<?php

declare(strict_types=1);

// ATL-231: `php artisan atlas:backup`, the RunBackup job and /api/backup.
return [
    'folder_create_failed' => 'The backup folder :folder does not exist and could not be created.',
    'file_exists' => 'A backup named :file already exists.',
    'empty_dump' => 'pg_dump finished but wrote an empty file.',
    'pg_dump_exit' => 'pg_dump exited with code :code',
    'already_running' => 'A backup is already running; nothing was started.',
    'done' => 'Backup written to :path (:bytes bytes).',
    'failed' => 'Backup failed: :error',
    'queued' => 'A backup will start in a moment.',
];
