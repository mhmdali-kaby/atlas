<?php

declare(strict_types=1);

namespace App\Http\Requests\Portfolio;

use App\Http\Requests\Settings\SettingsEnvRequest;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * GET /api/portfolio/performance?env=demo|live&range=1M|3M|YTD|ALL (range is case-insensitive, so "All" works).
 */
class PerformanceRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    protected function prepareForValidation(): void
    {
        $range = $this->query('range');
        if (is_string($range)) {
            $this->merge(['range' => strtoupper($range)]);
        }
    }

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'env' => ['sometimes', 'string', Rule::in(SettingsEnvRequest::envs())],
            'range' => ['sometimes', 'string', Rule::in(self::ranges())],
        ];
    }

    /**
     * @return list<string>
     */
    public static function ranges(): array
    {
        $ranges = config('atlas_defaults.portfolio_performance.ranges');

        return is_array($ranges) ? array_values(array_filter($ranges, 'is_string')) : ['1M', '3M', 'YTD', 'ALL'];
    }

    public function env(): string
    {
        $env = $this->validated('env') ?? config('atlas_defaults.env.default');

        return is_string($env) ? $env : 'demo';
    }

    public function range(): string
    {
        $range = $this->validated('range') ?? config('atlas_defaults.portfolio_performance.default_range');

        return is_string($range) ? $range : 'ALL';
    }
}
