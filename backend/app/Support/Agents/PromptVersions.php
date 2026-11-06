<?php

declare(strict_types=1);

namespace App\Support\Agents;

use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;

/**
 * ATL-251, brief §14.7, decision #265 (3): read side of prompt_versions, which the agents
 * supervisor fills (services/agents/src/atlas_agents/prompts.py, ATL-90). Only raw bodies
 * (preamble + role file, before any template is filled) are ever returned, never a rendered prompt.
 *
 * The "current file" version is the row whose sha256 equals sha256(_preamble.md bytes + <slug>.md
 * bytes) in services.agent_prompts.dir, the same rule prompts.load_source() uses.
 */
final class PromptVersions
{
    private const HASH_HEX = 12;

    public const PREAMBLE_FILE = '_preamble.md';

    /**
     * Newest first.
     *
     * @return list<array{version: int, hash12: string, created_at: string|null, runs_count: int, is_current_file: bool, is_pinned: bool}>
     */
    public static function list(string $slug, string $env, ?int $pinned): array
    {
        $current = self::currentFileSha($slug);
        $runs = DB::table('agent_runs')
            ->where('agent', $slug)
            ->where('env', $env)
            ->whereNotNull('prompt_version')
            ->groupBy('prompt_version')
            ->selectRaw('prompt_version, count(*) as n')
            ->pluck('n', 'prompt_version');

        $out = [];
        foreach (DB::table('prompt_versions')->where('agent', $slug)->orderByDesc('version')->get(['version', 'sha256', 'created_at']) as $row) {
            $version = (int) $row->version;
            $sha = (string) $row->sha256;
            $out[] = [
                'version' => $version,
                'hash12' => substr($sha, 0, self::HASH_HEX),
                'created_at' => is_string($row->created_at)
                    ? CarbonImmutable::parse($row->created_at)->utc()->toIso8601ZuluString('microsecond')
                    : null,
                'runs_count' => (int) ($runs[$version] ?? 0),
                'is_current_file' => $current !== null && hash_equals($sha, $current),
                'is_pinned' => $pinned === $version,
            ];
        }

        return $out;
    }

    /**
     * @return array{version: int, hash12: string, body: string}|null
     */
    public static function find(string $slug, int $version): ?array
    {
        $row = DB::table('prompt_versions')->where('agent', $slug)->where('version', $version)->first(['version', 'sha256', 'body']);
        if ($row === null) {
            return null;
        }

        return [
            'version' => (int) $row->version,
            'hash12' => substr((string) $row->sha256, 0, self::HASH_HEX),
            'body' => (string) $row->body,
        ];
    }

    public static function exists(string $slug, int $version): bool
    {
        return DB::table('prompt_versions')->where('agent', $slug)->where('version', $version)->exists();
    }

    /** sha256 hex of the agent's prompt files on disk, or null when either file is missing. */
    public static function currentFileSha(string $slug): ?string
    {
        $dir = config('services.agent_prompts.dir');
        if (! is_string($dir) || $dir === '' || preg_match('/^[a-z_]+$/', $slug) !== 1) {
            return null;
        }
        $preamble = rtrim($dir, '/').'/'.self::PREAMBLE_FILE;
        $role = rtrim($dir, '/')."/{$slug}.md";
        if (! is_file($preamble) || ! is_file($role)) {
            return null;
        }
        $a = file_get_contents($preamble);
        $b = file_get_contents($role);

        return $a === false || $b === false ? null : hash('sha256', $a.$b);
    }
}
