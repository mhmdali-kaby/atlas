<?php

declare(strict_types=1);

namespace App\Http\Requests\Proposals;

use Illuminate\Foundation\Http\FormRequest;

/** POST /api/proposals/{id}/ask-team { "question": "Why not wait for the CPI print?" } */
class AskTeamRequest extends FormRequest
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
        return ['question' => ['required', 'string', 'min:1', 'max:2000']];
    }

    public function question(): string
    {
        return trim((string) $this->validated('question'));
    }
}
