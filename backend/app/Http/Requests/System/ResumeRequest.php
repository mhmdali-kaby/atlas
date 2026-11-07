<?php

declare(strict_types=1);

namespace App\Http\Requests\System;

use Illuminate\Foundation\Http\FormRequest;

/** POST /api/system/resume — no body. */
class ResumeRequest extends FormRequest
{
    public function authorize(): bool
    {
        return $this->user()?->can('control-trading') ?? false;
    }

    /**
     * @return array<string, list<string>>
     */
    public function rules(): array
    {
        return [];
    }
}
