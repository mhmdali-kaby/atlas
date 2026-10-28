<?php

declare(strict_types=1);

namespace App\Http\Requests\Settings;

use App\Support\Settings\SettingsSchema;
use Brick\Math\BigDecimal;
use Closure;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * PATCH /api/settings: {env, version, changes}. Every field is checked against its §14 range;
 * decimals must be strings, integers must be JSON integers, unknown fields are refused.
 */
class UpdateSettingsRequest extends FormRequest
{
    private const DECIMAL_PATTERN = '/^\d{1,15}(\.\d{1,8})?$/';

    public function authorize(): bool
    {
        return true;
    }

    /**
     * @return array<string, list<mixed>|string>
     */
    public function rules(): array
    {
        $rules = [
            'env' => ['required', 'string', Rule::in(SettingsEnvRequest::envs())],
            'version' => ['required', 'integer:strict', 'min:1'],
            'changes' => ['required', 'array:'.implode(',', SettingsSchema::fields()), 'min:1'],
            'changes.risk_profile' => ['sometimes', 'string', Rule::in(SettingsSchema::PROFILES)],
        ];

        foreach (SettingsSchema::NUMERIC_FIELDS as $field => [$type, $min, $max, $exclusive]) {
            $rules["changes.{$field}"] = $type === 'integer'
                ? ['sometimes', 'integer:strict', "between:{$min},{$max}"]
                : ['sometimes', 'string', self::decimalRange((string) $min, (string) $max, $exclusive)];
        }

        return $rules;
    }

    /**
     * @return array<string, string>
     */
    public function attributes(): array
    {
        $attributes = [];
        foreach (SettingsSchema::fields() as $field) {
            $attributes["changes.{$field}"] = $field;
        }

        return $attributes;
    }

    public function env(): string
    {
        return (string) $this->validated('env');
    }

    public function version(): int
    {
        return (int) $this->validated('version');
    }

    /**
     * @return array<string, int|string>
     */
    public function changes(): array
    {
        /** @var array<string, int|string> $changes */
        $changes = $this->validated('changes');

        return $changes;
    }

    private static function decimalRange(string $min, string $max, bool $exclusive): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail) use ($min, $max, $exclusive): void {
            if (! is_string($value) || preg_match(self::DECIMAL_PATTERN, $value) !== 1) {
                $fail(__('settings.decimal_format'));

                return;
            }

            $number = BigDecimal::of($value);
            $tooLow = $exclusive ? $number->isLessThanOrEqualTo($min) : $number->isLessThan($min);

            if ($tooLow || $number->isGreaterThan($max)) {
                $fail(__($exclusive ? 'settings.decimal_range_exclusive' : 'settings.decimal_range_inclusive', [
                    'min' => $min,
                    'max' => $max,
                ]));
            }
        };
    }
}
