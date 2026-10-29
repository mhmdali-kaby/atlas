<?php

declare(strict_types=1);

namespace Database\Seeders;

use App\Models\NewsSource;
use Illuminate\Database\Seeder;

/**
 * Reference data, safe in production and idempotent: one news_sources row per source in
 * config/defaults.yaml `news.sources` (ATL-57). Existing rows are left alone, so the owner's
 * enabled flag, key and poll interval survive a re-seed.
 */
class NewsSourcesSeeder extends Seeder
{
    public function run(): void
    {
        /** @var array<string, array{kind: string, category: string, name: string, url: string|null, poll_seconds: int, requires_key: bool}> $sources */
        $sources = config('atlas_defaults.news.sources');

        foreach ($sources as $key => $source) {
            NewsSource::query()->firstOrCreate(
                ['key' => $key],
                [
                    'kind' => $source['kind'],
                    'category' => $source['category'],
                    'name' => $source['name'],
                    'url' => $source['url'],
                    'enabled' => true,
                    'poll_seconds' => $source['poll_seconds'],
                    'requires_key' => $source['requires_key'],
                ],
            );
        }
    }
}
