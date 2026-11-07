<?php

declare(strict_types=1);

namespace App\Http\Requests\DataSources;

use Illuminate\Foundation\Http\FormRequest;

/**
 * PATCH /api/data-sources/{key}: {enabled?, poll_seconds?}, at least one. poll_seconds is bounded
 * by config/defaults.yaml data_sources_api.poll_seconds_min/max. The key has its own endpoint.
 */
class UpdateDataSourceRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        $min = config('atlas_defaults.data_sources_api.poll_seconds_min');
        $max = config('atlas_defaults.data_sources_api.poll_seconds_max');

        return [
            'enabled' => ['required_without:poll_seconds', 'boolean'],
            'poll_seconds' => [
                'required_without:enabled', 'integer',
                'min:'.(is_int($min) ? $min : 60),
                'max:'.(is_int($max) ? $max : 86400),
            ],
            'api_key' => ['prohibited'],
            'key' => ['prohibited'],
            'requires_key' => ['prohibited'],
            // ATL-106: trust flags come from config/defaults.yaml only; the owner cannot raise a
            // source to reputable/official and turn its rumours into CONFIRMED news.
            'reputable' => ['prohibited'],
            'official' => ['prohibited'],
        ];
    }
}
