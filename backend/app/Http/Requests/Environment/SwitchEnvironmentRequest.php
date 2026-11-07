<?php

declare(strict_types=1);

namespace App\Http\Requests\Environment;

use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * POST /api/environment/switch (ATL-236, decision #255 (5)). The shape only: to live needs a
 * password and a confirmation string, to demo needs confirm: true. Whether the password is right,
 * the confirmation is exactly "TRADE LIVE" and the pre-flight passes is checked by the controller.
 */
class SwitchEnvironmentRequest extends FormRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'target' => ['required', 'string', Rule::in(['demo', 'live'])],
            'password' => ['exclude_unless:target,live', 'required', 'string', 'max:255'],
            'confirmation' => ['exclude_unless:target,live', 'required', 'string', 'max:64'],
            'confirm' => ['exclude_unless:target,demo', 'required', 'accepted'],
        ];
    }

    /**
     * @return array<string, string>
     */
    public function attributes(): array
    {
        return [
            'target' => (string) __('environment.fields.target'),
            'password' => (string) __('environment.fields.password'),
            'confirmation' => (string) __('environment.fields.confirmation'),
            'confirm' => (string) __('environment.fields.confirm'),
        ];
    }

    /** @return 'demo'|'live' */
    public function target(): string
    {
        return $this->string('target')->toString() === 'live' ? 'live' : 'demo';
    }
}
