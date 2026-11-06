<?php

declare(strict_types=1);

namespace App\Http\Requests\TwoFactor;

use App\Models\User;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Support\Facades\Hash;
use Illuminate\Validation\Validator;

/**
 * Start, regenerate and disable on /api/me/two-factor (ATL-245): each needs the owner's password.
 * A wrong password is a 422 on `password`; the password itself is never logged or echoed.
 */
class TwoFactorPasswordRequest extends FormRequest
{
    /**
     * @return array<string, list<string>>
     */
    public function rules(): array
    {
        return ['password' => ['required', 'string', 'max:1024']];
    }

    /**
     * @return list<callable(Validator): void>
     */
    public function after(): array
    {
        return [
            function (Validator $validator): void {
                if ($validator->errors()->isNotEmpty()) {
                    return;
                }
                $user = $this->user();
                if (! $user instanceof User || ! Hash::check($this->string('password')->toString(), $user->password)) {
                    $validator->errors()->add('password', (string) __('two_factor.password_wrong'));
                }
            },
        ];
    }

    /**
     * @return array<string, string>
     */
    public function attributes(): array
    {
        return ['password' => (string) __('two_factor.fields.password')];
    }
}
