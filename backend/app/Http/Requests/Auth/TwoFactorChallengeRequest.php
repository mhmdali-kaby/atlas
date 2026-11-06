<?php

declare(strict_types=1);

namespace App\Http\Requests\Auth;

use Illuminate\Foundation\Http\FormRequest;

/**
 * POST /api/auth/two-factor-challenge (ATL-246): the code from the authenticator app, or one
 * recovery code. Whether it is right is decided by the TwoFactor service in the controller.
 */
class TwoFactorChallengeRequest extends FormRequest
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
        return [
            'code' => ['required_without:recovery_code', 'nullable', 'string', 'max:16'],
            'recovery_code' => ['required_without:code', 'nullable', 'string', 'max:64'],
        ];
    }

    /**
     * @return array<string, string>
     */
    public function attributes(): array
    {
        return [
            'code' => (string) __('two_factor.fields.code'),
            'recovery_code' => (string) __('two_factor.fields.recovery_code'),
        ];
    }

    /** The authenticator code, or null when the owner sent a recovery code instead. */
    public function code(): ?string
    {
        $code = $this->input('code');

        return is_string($code) && $code !== '' ? $code : null;
    }

    public function recoveryCode(): string
    {
        return $this->string('recovery_code')->toString();
    }
}
