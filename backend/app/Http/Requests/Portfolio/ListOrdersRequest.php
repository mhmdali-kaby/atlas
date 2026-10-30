<?php

declare(strict_types=1);

namespace App\Http\Requests\Portfolio;

use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * GET /api/portfolio/orders?status=open|algo (ATL-148). Missing = both lists. Always the active env.
 */
class ListOrdersRequest extends FormRequest
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
            'status' => ['sometimes', 'string', Rule::in(['open', 'algo'])],
        ];
    }

    public function wants(string $status): bool
    {
        $value = $this->validated('status');

        return ! is_string($value) || $value === $status;
    }
}
