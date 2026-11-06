<?php

declare(strict_types=1);

namespace App\Http\Requests\TwoFactor;

use Illuminate\Foundation\Http\FormRequest;

/**
 * POST /api/me/two-factor/confirm (ATL-245): the 6-digit code from the authenticator app.
 * Whether the code is right is decided by the TwoFactor service in the controller.
 */
class ConfirmTwoFactorRequest extends FormRequest
{
    /**
     * @return array<string, list<string>>
     */
    public function rules(): array
    {
        return ['code' => ['required', 'string', 'max:16']];
    }

    /**
     * @return array<string, string>
     */
    public function attributes(): array
    {
        return ['code' => (string) __('two_factor.fields.code')];
    }

    public function code(): string
    {
        return $this->string('code')->toString();
    }
}
