<?php

declare(strict_types=1);

namespace App\Http\Requests\System;

use Illuminate\Foundation\Http\FormRequest;

/**
 * POST /api/system/kill {"confirm": "KILL", "flatten"?: bool}. `confirm` is a fixed guard the
 * kill-switch sheet sends on its Stop button (decision #251: the owner never types it), so a stray
 * POST cannot trip the kill switch. `flatten` = also close every position.
 */
class KillRequest extends FormRequest
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
        return [
            'confirm' => ['required', 'string', 'in:KILL'],
            'flatten' => ['sometimes', 'boolean'],
        ];
    }

    /**
     * @return array<string, string>
     */
    public function messages(): array
    {
        return [
            'confirm.required' => __('system.confirm_kill'),
            'confirm.in' => __('system.confirm_kill'),
        ];
    }

    public function flatten(): bool
    {
        return $this->boolean('flatten');
    }
}
