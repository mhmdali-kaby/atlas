<?php

declare(strict_types=1);

namespace App\Http\Requests\System;

use Illuminate\Foundation\Http\FormRequest;

/**
 * POST /api/system/pause {"reason"?: string ≤ 500}. trading_core defaults the reason to
 * "Paused by the owner." when it is missing.
 */
class PauseRequest extends FormRequest
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
            'reason' => ['sometimes', 'nullable', 'string', 'max:500'],
        ];
    }

    /** @return array<string, string> the body for trading_core */
    public function payload(): array
    {
        $reason = $this->validated('reason');

        return is_string($reason) && trim($reason) !== '' ? ['reason' => trim($reason)] : [];
    }
}
