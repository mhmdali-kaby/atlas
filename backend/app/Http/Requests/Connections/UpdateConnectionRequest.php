<?php

declare(strict_types=1);

namespace App\Http\Requests\Connections;

use Illuminate\Foundation\Http\FormRequest;

/**
 * PATCH /api/connections/{id}. Every field is optional; an omitted secret keeps its stored value.
 * exchange and env cannot be changed (delete and re-create instead).
 */
class UpdateConnectionRequest extends FormRequest
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
            'exchange' => ['prohibited'],
            'env' => ['prohibited'],
            'label' => ['sometimes', 'required', 'string', 'max:100'],
            'api_key' => ['sometimes', 'required', 'string', 'min:4', 'max:255'],
            'api_secret' => ['sometimes', 'required', 'string', 'max:255'],
            'passphrase' => ['sometimes', 'required', 'string', 'max:255'],
        ];
    }
}
