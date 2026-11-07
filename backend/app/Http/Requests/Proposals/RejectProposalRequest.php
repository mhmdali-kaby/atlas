<?php

declare(strict_types=1);

namespace App\Http\Requests\Proposals;

use Illuminate\Foundation\Http\FormRequest;

/** POST /api/proposals/{id}/reject { "reason": "Too close to CPI." } */
class RejectProposalRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    /**
     * @return array<string, list<string>>
     */
    public function rules(): array
    {
        return ['reason' => ['required', 'string', 'min:1', 'max:1000']];
    }

    public function reason(): string
    {
        return trim((string) $this->validated('reason'));
    }
}
