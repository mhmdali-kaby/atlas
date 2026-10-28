<?php

declare(strict_types=1);

namespace App\Http\Requests\Portfolio;

use App\Http\Requests\Settings\SettingsEnvRequest;
use App\Support\Portfolio\PortfolioService;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * Query string shared by every /api/portfolio/* endpoint:
 * ?env=demo|live (defaults to the default env), ?range= (equity), ?per_page=&kind= (cashflows).
 */
class PortfolioRequest extends FormRequest
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
            'range' => ['sometimes', 'string', Rule::in(PortfolioService::RANGES)],
            'per_page' => ['sometimes', 'integer', 'min:1', 'max:100'],
            'kind' => ['sometimes', 'string', Rule::in(['deposit', 'withdrawal'])],
        ];
    }

    public function env(): string
    {
        $env = $this->validated('env') ?? config('atlas_defaults.env.default');

        return is_string($env) ? $env : 'demo';
    }

    public function range(): string
    {
        $range = $this->validated('range');

        return is_string($range) ? $range : '1M';
    }

    public function perPage(): int
    {
        $perPage = $this->validated('per_page');

        return is_numeric($perPage) ? (int) $perPage : 20;
    }

    public function kind(): ?string
    {
        $kind = $this->validated('kind');

        return is_string($kind) ? $kind : null;
    }
}
