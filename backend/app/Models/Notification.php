<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Table;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Carbon;

/**
 * One owner notification (brief §17, ATL-214), stored by atlas:bridge from a bus envelope.
 *
 * @property string $id UUID
 * @property 'demo'|'live' $env
 * @property string $event one of notifications.events
 * @property 'info'|'warning'|'critical' $severity
 * @property string $title_key i18n key, e.g. notifications.events.order_filled.title
 * @property array<string, bool|int|float|string|list<string>|null> $params
 * @property string|null $link SPA path
 * @property string $source_event_id the bus envelope id
 * @property array<string, bool> $channels
 * @property Carbon|null $read_at
 * @property Carbon $created_at
 */
#[Table(name: 'notifications')]
#[Fillable(['env', 'event', 'severity', 'title_key', 'params', 'link', 'source_event_id', 'channels', 'read_at', 'created_at'])]
class Notification extends Model
{
    use HasUuids;

    public const SEVERITIES = ['info', 'warning', 'critical'];

    public const UPDATED_AT = null;

    protected $dateFormat = 'Y-m-d H:i:s.uP';

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'params' => 'array',
            'channels' => 'array',
            'read_at' => 'datetime',
            'created_at' => 'datetime',
        ];
    }
}
