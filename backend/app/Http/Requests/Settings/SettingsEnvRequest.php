<?php

declare(strict_types=1);

namespace App\Http\Requests\Settings;

use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * GET /api/settings and /api/settings/history: ?env=demo|live (defaults to the default env).
 */
class SettingsEnvRequest extends FormRequest
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
        return [
            'env' => ['sometimes', 'string', Rule::in(self::envs())],
            'per_page' => ['sometimes', 'integer', 'min:1', 'max:100'],
        ];
    }

    public function env(): string
    {
        $env = $this->validated('env') ?? config('atlas_defaults.env.default');

        return is_string($env) ? $env : 'demo';
    }

    /**
     * @return list<string>
     */
    public static function envs(): array
    {
        $allowed = config('atlas_defaults.env.allowed');

        return is_array($allowed) ? array_values(array_map(strval(...), $allowed)) : ['demo', 'live'];
    }
}
