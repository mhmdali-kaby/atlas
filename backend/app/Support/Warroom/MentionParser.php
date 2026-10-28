<?php

declare(strict_types=1);

namespace App\Support\Warroom;

/**
 * Resolves `@Agent` mentions in War Room text to the nine agent keys in
 * `atlas_defaults.warroom.agents` (brief §7). Matching is case-insensitive and ignores "_" and
 * "-", so "@Historian", "@market_scout" and "@MarketScout" all resolve; each agent's configured
 * aliases ("@Technical", "@PM") resolve too. Unknown names are ignored.
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
     * @return list<string>
     */
    public static function agents(): array
    {
        $agents = config('atlas_defaults.warroom.agents');

        return is_array($agents) ? array_map(strval(...), array_keys($agents)) : [];
    }

    /**
     * @return array<string, string> normalised spelling => agent key
     */
    private static function lookup(): array
    {
        $agents = config('atlas_defaults.warroom.agents');
        if (! is_array($agents)) {
            return [];
        }

        $lookup = [];
        foreach ($agents as $key => $aliases) {
            $key = (string) $key;
            $lookup[self::normalise($key)] = $key;
            foreach (is_array($aliases) ? $aliases : [] as $alias) {
                if (is_string($alias)) {
                    $lookup[self::normalise($alias)] = $key;
                }
            }
        }

        return $lookup;
    }

    private static function normalise(string $name): string
    {
        return strtolower(str_replace(['_', '-'], '', $name));
    }
}
