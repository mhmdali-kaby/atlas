<?php

declare(strict_types=1);

namespace App\Http\Requests\Connections;

use App\Http\Requests\Settings\SettingsEnvRequest;
use App\Models\Connection;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;
use Illuminate\Validation\Validator;

/**
 * POST /api/connections. Phase 0 accepts only env=demo; live is refused with 422 (brief §20).
 */
class StoreConnectionRequest extends FormRequest
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
            'exchange' => ['sometimes', 'string', Rule::in(['okx'])],
            'env' => ['required', 'string', Rule::in(SettingsEnvRequest::envs())],
            'label' => ['required', 'string', 'max:100'],
            'api_key' => ['required', 'string', 'min:4', 'max:255'],
            'api_secret' => ['required', 'string', 'max:255'],
            'passphrase' => ['required', 'string', 'max:255'],
        ];
    }

    /**
     * @return list<callable(Validator): void>
     */
    public function after(): array
    {
        return [
            function (Validator $validator): void {
                if ($validator->errors()->has('env')) {
                    return;
                }
                $env = $this->input('env');
                if ($env === 'live') {
                    $validator->errors()->add('env', __('connections.live_disabled'));

                    return;
                }
                $exists = Connection::query()
                    ->where('exchange', $this->exchange())
                    ->where('env', $env)
                    ->exists();
                if ($exists) {
                    $validator->errors()->add('env', __('connections.already_exists'));
                }
            },
        ];
    }

    public function exchange(): string
    {
        $exchange = $this->input('exchange', 'okx');

        return is_string($exchange) ? $exchange : 'okx';
    }
}
