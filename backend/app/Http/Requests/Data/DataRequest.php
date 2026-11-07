<?php

declare(strict_types=1);

namespace App\Http\Requests\Data;

use App\Http\Requests\Settings\SettingsEnvRequest;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * GET /api/data/backfill?env= and GET /api/data/storage: ?env=demo|live, defaulting to the
 * default env, like the Settings, Portfolio and War Room APIs.
 */
class DataRequest extends FormRequest
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
            'env' => ['sometimes', 'string', Rule::in(SettingsEnvRequest::envs())],
        ];
    }

    /**
     * @return 'demo'|'live'
     */
    public function env(): string
    {
        $env = $this->input('env', config('atlas_defaults.env.default'));

        return $env === 'live' ? 'live' : 'demo';
    }
}
