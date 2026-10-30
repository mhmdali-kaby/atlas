<?php

declare(strict_types=1);

namespace App\Http\Requests\Portfolio;

use App\Support\Portfolio\BenchmarkService;
use Illuminate\Validation\Rule;

/**
 * GET /api/portfolio/benchmark: ?symbol=BTC|SPY|XAU (required), plus the shared ?env= and ?range=.
 */
class BenchmarkRequest extends PortfolioRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            ...parent::rules(),
            'symbol' => ['required', 'string', Rule::in(BenchmarkService::symbols())],
        ];
    }

    public function symbol(): string
    {
        $symbol = $this->validated('symbol');

        return is_string($symbol) ? $symbol : '';
    }
}
