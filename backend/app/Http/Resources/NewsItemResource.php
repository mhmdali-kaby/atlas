<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Casts\PostgresTextArray;
use App\Support\News\NewsFormat;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * One news_items row from the query builder (ATL-67). Titles are returned as plain data; the
 * API never renders HTML (brief §1.7). In ?cluster=1 mode the row also carries item_count and
 * first_seen for its cluster (or for itself when it has no cluster).
 */
class NewsItemResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var object $row */
        $row = $this->resource;
        $data = (array) $row;

        $item = [
            'id' => $data['id'],
            'title' => $data['title'],
            'title_original' => $data['title_original'] ?? null,
            'language' => $data['language'] ?? null,
            'source' => $data['source'],
            'url' => $data['url'],
            'published_at' => NewsFormat::timestamp($data['published_at']),
            'instruments' => PostgresTextArray::parse(is_string($data['instruments']) ? $data['instruments'] : '{}'),
            'asset_classes' => PostgresTextArray::parse(is_string($data['asset_classes']) ? $data['asset_classes'] : '{}'),
            'event_type' => $data['event_type'] ?? null,
            'sentiment' => NewsFormat::float($data['sentiment'] ?? null),
            'importance' => (int) $data['importance'],
            'novelty' => NewsFormat::float($data['novelty'] ?? null),
            'cluster_id' => $data['cluster_id'] ?? null,
        ];

        if (array_key_exists('item_count', $data)) {
            $item['item_count'] = (int) $data['item_count'];
            $item['first_seen'] = NewsFormat::timestamp($data['first_seen']);
        }

        return $item;
    }
}
