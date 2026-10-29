<?php

declare(strict_types=1);

namespace App\Support\Warroom;

/**
 * Resolves `@Agent` mentions in War Room text to the nine agent keys in
 * `atlas_defaults.warroom.agents` (brief §7). Matching is case-insensitive and ignores "_" and
 * "-", so "@Historian", "@market_scout" and "@MarketScout" all resolve, as do each agent's
 * one-word handle ("@Scout", "@Devil", "@PM") and configured aliases. Unknown names are ignored.
 */
final class MentionParser
{
    /**
     * @return list<string> agent keys, in order of first mention, without duplicates
     */
    public function parse(string $text): array
    {
        if (preg_match_all('/(?<![\p{L}\p{N}_@])@([A-Za-z][A-Za-z0-9_\-]*)/u', $text, $matches) === false) {
            return [];
        }

        $lookup = self::lookup();
        $found = [];
        foreach ($matches[1] as $name) {
            $agent = $lookup[self::normalise($name)] ?? null;
            if ($agent !== null && ! in_array($agent, $found, true)) {
                $found[] = $agent;
            }
        }

        return $found;
    }

    /**
     * @return list<string> agent slugs, in brief §7 order
     */
    public static function agents(): array
    {
        return array_column(self::directory(), 'slug');
    }

    /**
     * The nine agents with their display name, one-word @handle and extra aliases, in brief §7
     * order (config/defaults.yaml warroom.agents). Served by GET /api/warroom/agents.
     *
     * @return list<array{slug: string, name: string, handle: string, aliases: list<string>}>
     */
    public static function directory(): array
    {
        $agents = config('atlas_defaults.warroom.agents');
        if (! is_array($agents)) {
            return [];
        }

        $out = [];
        foreach ($agents as $slug => $agent) {
            $slug = (string) $slug;
            $agent = is_array($agent) ? $agent : [];
            $aliases = is_array($agent['aliases'] ?? null) ? $agent['aliases'] : [];
            $out[] = [
                'slug' => $slug,
                'name' => is_string($agent['name'] ?? null) ? $agent['name'] : $slug,
                'handle' => is_string($agent['handle'] ?? null) ? $agent['handle'] : $slug,
                'aliases' => array_values(array_filter($aliases, is_string(...))),
            ];
        }

        return $out;
    }

    /**
     * @return array<string, string> normalised spelling => agent slug
     */
    private static function lookup(): array
    {
        $lookup = [];
        foreach (self::directory() as $agent) {
            foreach ([$agent['slug'], $agent['name'], $agent['handle'], ...$agent['aliases']] as $spelling) {
                $lookup[self::normalise($spelling)] = $agent['slug'];
            }
        }

        return $lookup;
    }

    private static function normalise(string $name): string
    {
        return strtolower((string) preg_replace('/[^A-Za-z0-9]/', '', $name));
    }
}
