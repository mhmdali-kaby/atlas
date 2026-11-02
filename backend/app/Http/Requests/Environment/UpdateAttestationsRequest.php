<?php

declare(strict_types=1);

namespace App\Http\Requests\Environment;

use App\Support\System\Preflight;
use Illuminate\Foundation\Http\FormRequest;

/**
 * PUT /api/environment/attestations (ATL-236): {"ip_allowlist": true, "sub_account": false, …}.
 * Every key is optional; true sets confirmed_at to now (kept when already set), false clears it.
 */
class UpdateAttestationsRequest extends FormRequest
{
    /**
     * @return array<string, list<string>>
     */
    public function rules(): array
    {
        $rules = [];
        foreach (Preflight::ATTESTED as $key) {
            $rules[$key] = ['sometimes', 'required', 'boolean:strict'];
        }

        return $rules;
    }

    /**
     * @return array<string, string>
     */
    public function attributes(): array
    {
        $attributes = [];
        foreach (Preflight::ATTESTED as $key) {
            $attributes[$key] = (string) __("environment.fields.{$key}");
        }

        return $attributes;
    }

    /**
     * @return array<string, bool>
     */
    public function changes(): array
    {
        /** @var array<string, mixed> $validated */
        $validated = $this->validated();
        $changes = [];
        foreach (Preflight::ATTESTED as $key) {
            if (array_key_exists($key, $validated)) {
                $changes[$key] = $validated[$key] === true;
            }
        }

        return $changes;
    }
}
