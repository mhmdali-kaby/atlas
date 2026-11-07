<?php

declare(strict_types=1);

namespace App\Http\Requests\DataSources;

use Illuminate\Foundation\Http\FormRequest;

/**
 * PUT /api/data-sources/{key}/key: {api_key}. Trimmed by the global TrimStrings middleware; no
 * whitespace inside, at least 4 characters (the UI shows the last 4), bounded by
 * data_sources_api.api_key_max_length. Validation messages never echo the value.
 */
class SetDataSourceKeyRequest extends FormRequest
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
        $max = config('atlas_defaults.data_sources_api.api_key_max_length');

        return [
            'api_key' => ['required', 'string', 'min:4', 'max:'.(is_int($max) ? $max : 255), 'regex:/^\S+$/'],
        ];
    }

    public function apiKey(): string
    {
        $value = $this->validated('api_key');

        return is_string($value) ? $value : '';
    }
}
