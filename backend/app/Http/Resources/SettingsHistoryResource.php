<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\SettingsHistory;
use App\Support\Settings\SettingsSchema;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * @mixin SettingsHistory
 */
class SettingsHistoryResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var SettingsHistory $row */
        $row = $this->resource;

        return [
            'id' => $row->id,
            'version' => $row->version,
            'settings' => self::ordered($row->data),
            'diff' => (object) array_map(
                fn (array $change): array => ['from' => $change['from'], 'to' => $change['to']],
                self::ordered($row->diff),
            ),
            'changed_by' => $row->changedBy === null ? null : [
                'id' => $row->changedBy->id,
                'name' => $row->changedBy->name,
            ],
            'created_at' => $row->created_at->toIso8601ZuluString('microsecond'),
        ];
    }

    /**
     * jsonb does not keep key order; give fields back in schema order.
     *
     * @template T
     *
     * @param  array<string, T>  $values
     * @return array<string, T>
     */
    private static function ordered(array $values): array
    {
        $out = [];
        foreach (SettingsSchema::fields() as $field) {
            if (array_key_exists($field, $values)) {
                $out[$field] = $values[$field];
            }
        }

        return $out + $values;
    }
}
