<?php

declare(strict_types=1);

namespace App\Http\Requests\Preferences;

use App\Models\User;
use DateTimeZone;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * PATCH /api/me/preferences (ATL-226). Every field is optional; only the ones sent change.
 */
class UpdatePreferencesRequest extends FormRequest
{
    public const FIELDS = ['name', 'locale', 'timezone', 'theme'];

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'name' => ['sometimes', 'required', 'string', 'min:1', 'max:80', 'not_regex:/^\s*$/'],
            'locale' => ['sometimes', 'required', 'string', Rule::in(User::LOCALES)],
            // Exact IANA identifiers only (no aliases or offsets), so the SPA's Intl APIs accept it.
            'timezone' => ['sometimes', 'required', 'string', Rule::in(DateTimeZone::listIdentifiers())],
            'theme' => ['sometimes', 'required', 'string', Rule::in(User::THEMES)],
        ];
    }

    /**
     * @return array<string, string>
     */
    public function attributes(): array
    {
        $attributes = [];
        foreach (self::FIELDS as $field) {
            $attributes[$field] = (string) __("preferences.fields.{$field}");
        }

        return $attributes;
    }

    /**
     * @return array<string, string>
     */
    public function messages(): array
    {
        return [
            'timezone.in' => (string) __('preferences.timezone_invalid'),
        ];
    }

    /**
     * @return array<string, string>
     */
    public function changes(): array
    {
        /** @var array<string, mixed> $validated */
        $validated = $this->validated();
        $changes = [];
        foreach (self::FIELDS as $field) {
            if (array_key_exists($field, $validated)) {
                $value = (string) $validated[$field];
                $changes[$field] = $field === 'name' ? trim($value) : $value;
            }
        }

        return $changes;
    }
}
