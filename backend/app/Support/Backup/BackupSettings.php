<?php

declare(strict_types=1);

namespace App\Support\Backup;

use App\Models\Setting;
use App\Support\Settings\SettingsService;
use App\Support\System\ActiveEnv;
use Throwable;

/**
 * ATL-231, decision #255 (3): the four `backup.*` settings keys, their defaults (config/defaults.yaml
 * `backup`) and the folder rule. Settings rows are per env but a backup is of the whole database,
 * so the scheduler, the command and GET /api/backup read the active env's row.
 *
 * backup.folder must be an absolute path that exists or can be created, is writable, and is not
 * inside the repository (a dump there would end up next to the code, and in git if ignored wrongly).
 */
final class BackupSettings
{
    public const ENABLED = 'backup.enabled';

    public const FOLDER = 'backup.folder';

    public const TIME = 'backup.time';

    public const KEEP = 'backup.keep';

    public const TIME_PATTERN = '/^([01]\d|2[0-3]):[0-5]\d$/';

    /** The dump files atlas:backup writes, and the only files its pruning may delete. */
    public const FILE_PATTERN = '/^atlas-\d{8}-\d{6}\.dump$/';

    public const FOLDER_MAX_LENGTH = 1024;

    /**
     * @return list<string>
     */
    public static function fields(): array
    {
        return [self::ENABLED, self::FOLDER, self::TIME, self::KEEP];
    }

    /**
     * @return array{'backup.enabled': bool, 'backup.folder': string, 'backup.time': string, 'backup.keep': int}
     */
    public static function defaults(): array
    {
        $folder = config('atlas_defaults.backup.folder');
        $time = config('atlas_defaults.backup.time');
        $keep = config('atlas_defaults.backup.keep');

        return [
            self::ENABLED => config('atlas_defaults.backup.enabled') !== false,
            self::FOLDER => is_string($folder) && $folder !== '' ? self::normaliseFolder($folder) : self::defaultFolder(),
            self::TIME => is_string($time) ? $time : '03:00',
            self::KEEP => is_int($keep) ? $keep : 14,
        ];
    }

    /** `<repo>/../backups`: beside the repository, never inside it. */
    public static function defaultFolder(): string
    {
        $name = config('atlas_defaults.backup.default_folder_name');

        return dirname(self::repositoryRoot()).'/'.(is_string($name) && $name !== '' ? $name : 'backups');
    }

    /** The repository root (the parent of backend/). */
    public static function repositoryRoot(): string
    {
        $root = dirname(base_path());

        return realpath($root) ?: $root;
    }

    /**
     * @return array{0: int, 1: int}
     */
    public static function keepBounds(): array
    {
        $min = config('atlas_defaults.backup.keep_min');
        $max = config('atlas_defaults.backup.keep_max');

        return [is_int($min) ? $min : 1, is_int($max) ? $max : 90];
    }

    /** One backup.* value in stored form. */
    public static function normalise(string $field, mixed $value): bool|int|string
    {
        return match ($field) {
            self::ENABLED => $value === true,
            self::KEEP => is_numeric($value) ? (int) $value : 0,
            self::FOLDER => is_string($value) ? self::normaliseFolder($value) : '',
            default => is_string($value) ? $value : '',
        };
    }

    /** Trimmed, with trailing slashes dropped (except for `/` itself). */
    public static function normaliseFolder(string $path): string
    {
        $path = trim($path);
        $trimmed = rtrim($path, '/');

        return $trimmed === '' && str_starts_with($path, '/') ? '/' : $trimmed;
    }

    /**
     * Why a folder cannot hold backups, as a `settings.*` translation key; null when it can.
     */
    public static function folderProblem(string $path): ?string
    {
        $path = self::normaliseFolder($path);
        if ($path === '' || ! str_starts_with($path, '/') || strlen($path) > self::FOLDER_MAX_LENGTH
            || str_contains($path, "\0") || preg_match('#(^|/)\.\.?(/|$)#', $path) === 1) {
            return 'settings.backup_folder_absolute';
        }

        $resolved = self::resolve($path);
        if ($resolved === null) {
            return 'settings.backup_folder_unwritable';
        }
        [$real, $existing] = $resolved;

        $repo = self::repositoryRoot();
        if ($real === $repo || str_starts_with($real, rtrim($repo, '/').'/')) {
            return 'settings.backup_folder_inside_repo';
        }

        // The folder itself when it exists, else the directory it would be created in. A file at
        // the path (or in the way) fails is_dir.
        if (! is_dir($existing) || ! is_writable($existing)) {
            return 'settings.backup_folder_unwritable';
        }

        return null;
    }

    /**
     * The path with its nearest existing ancestor resolved through symlinks, and that ancestor.
     * A symlink inside the folder's existing part pointing into the repository is caught this way.
     *
     * @return array{0: string, 1: string}|null [resolved path, nearest existing directory]
     */
    private static function resolve(string $path): ?array
    {
        $rest = [];
        $current = $path;
        while (! file_exists($current)) {
            $rest[] = basename($current);
            $parent = dirname($current);
            if ($parent === $current) {
                return null;
            }
            $current = $parent;
        }
        $base = realpath($current);
        if ($base === false) {
            return null;
        }
        $real = $rest === [] ? $base : rtrim($base, '/').'/'.implode('/', array_reverse($rest));

        return [$real, $base];
    }

    /**
     * The values in force: the active env's settings row, with defaults for anything missing (or
     * when the database is not reachable, so `schedule:list` still works).
     *
     * @return array{enabled: bool, folder: string, time: string, keep: int}
     */
    public static function effective(): array
    {
        $values = self::defaults();
        try {
            $row = Setting::query()->where('env', ActiveEnv::get())->first();
            if ($row !== null) {
                $stored = app(SettingsService::class)->values($row);
                foreach (self::fields() as $field) {
                    if (array_key_exists($field, $stored)) {
                        $values[$field] = self::normalise($field, $stored[$field]);
                    }
                }
            }
        } catch (Throwable) {
            // No database yet: the defaults.
        }

        $time = is_string($values[self::TIME]) && preg_match(self::TIME_PATTERN, $values[self::TIME]) === 1
            ? $values[self::TIME] : self::defaults()[self::TIME];

        return [
            'enabled' => $values[self::ENABLED] === true,
            'folder' => is_string($values[self::FOLDER]) && $values[self::FOLDER] !== '' ? $values[self::FOLDER] : self::defaultFolder(),
            'time' => $time,
            'keep' => is_int($values[self::KEEP]) && $values[self::KEEP] >= 1 ? $values[self::KEEP] : self::defaults()[self::KEEP],
        ];
    }
}
