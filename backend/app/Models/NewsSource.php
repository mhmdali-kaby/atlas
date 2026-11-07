<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Hidden;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * One news/calendar/sentiment source polled by atlas_ingest (ATL-57, decision #243). Rows come
 * from NewsSourcesSeeder; the owner toggles them and enters keys in Settings → Data sources.
 * api_key is stored as Laravel `encrypted` ciphertext, never returned by an API (last 4 only).
 *
 * @property string $id UUID
 * @property string $key e.g. rss.coindesk, cryptopanic, finnhub.news
 * @property string $provider vendor that owns the API key, e.g. finnhub (ATL-71); shared by its sources
 * @property 'rss'|'api'|'calendar'|'earnings'|'sentiment' $kind transport
 * @property 'exchange'|'crypto_news'|'markets_news'|'calendar'|'earnings'|'sentiment' $category topic the UI groups by
 * @property string $name
 * @property string|null $url
 * @property bool $enabled
 * @property int $poll_seconds
 * @property bool $requires_key
 * @property bool $reputable newsroom with editorial standards (ATL-106); read-only, from config
 * @property bool $official the venue's own channel, e.g. OKX announcements (ATL-106); read-only, from config
 * @property string|null $api_key plaintext through the model; ciphertext in the column
 * @property int $consecutive_failures
 * @property Carbon|null $disabled_until
 * @property Carbon|null $last_success_at
 * @property Carbon|null $last_item_at
 * @property string|null $last_error
 * @property Carbon $created_at
 * @property Carbon $updated_at
 */
#[Table(name: 'news_sources')]
#[Fillable(['key', 'provider', 'kind', 'category', 'name', 'url', 'enabled', 'poll_seconds', 'requires_key', 'api_key'])]
#[Hidden(['api_key'])]
class NewsSource extends Model
{
    use HasUuids;

    public const KINDS = ['rss', 'api', 'calendar', 'earnings', 'sentiment'];

    public const CATEGORIES = ['exchange', 'crypto_news', 'markets_news', 'calendar', 'earnings', 'sentiment'];

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    public function apiKeyLast4(): ?string
    {
        return $this->api_key === null ? null : mb_substr($this->api_key, -4);
    }

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'enabled' => 'boolean',
            'requires_key' => 'boolean',
            'reputable' => 'boolean',
            'official' => 'boolean',
            'poll_seconds' => 'integer',
            'api_key' => 'encrypted',
            'consecutive_failures' => 'integer',
            'disabled_until' => 'datetime',
            'last_success_at' => 'datetime',
            'last_item_at' => 'datetime',
        ];
    }
}
