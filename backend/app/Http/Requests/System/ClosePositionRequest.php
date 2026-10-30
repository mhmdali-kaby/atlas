<?php

declare(strict_types=1);

namespace App\Http\Requests\System;

use Illuminate\Foundation\Http\FormRequest;

/** POST /api/portfolio/positions/{id}/close {"confirm": true}. */
class ClosePositionRequest extends FormRequest
{
    public function authorize(): bool
    {
        return $this->user()?->can('close-position') ?? false;
    }

    /**
     * @return array<string, list<string>>
     */
    public function rules(): array
    {
        return [
            'confirm' => ['required', 'accepted'],
        ];
    }

    /**
     * @return array<string, string>
     */
    public function messages(): array
    {
        return [
            'confirm.required' => __('system.confirm_close'),
            'confirm.accepted' => __('system.confirm_close'),
        ];
    }
}
