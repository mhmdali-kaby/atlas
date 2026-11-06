<?php

declare(strict_types=1);

namespace App\Http\Requests\Agents;

use App\Http\Requests\Settings\SettingsEnvRequest;
use App\Support\System\ActiveEnv;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * PUT /api/agents/{slug}/prompt-pin: {version: int|null, env?: demo|live}. Env defaults to the
 * active env, the one the agents supervisor runs in.
 */
class PinPromptRequest extends FormRequest
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
            'version' => ['present', 'nullable', 'integer:strict', 'min:1'],
            'env' => ['sometimes', 'string', Rule::in(SettingsEnvRequest::envs())],
        ];
    }

    public function env(): string
    {
        $env = $this->validated('env');

        return is_string($env) ? $env : ActiveEnv::get();
    }

    public function version(): ?int
    {
        $version = $this->validated('version');

        return is_int($version) ? $version : null;
    }
}
