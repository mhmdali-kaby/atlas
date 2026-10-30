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
            // Not `array:<keys>`: the validator swaps literal dots in input keys for a placeholder,
            // so the agent keys (`agents.historian.enabled`) are checked against the raw input.
            'changes' => ['required', 'array', 'min:1', $this->knownFields()],
            'changes.risk_profile' => ['sometimes', 'string', Rule::in(SettingsSchema::PROFILES)],
        ];

        foreach (SettingsSchema::NUMERIC_FIELDS as $field => [$type, $min, $max, $exclusive]) {
            $rules["changes.{$field}"] = $type === 'integer'
                ? ['sometimes', 'integer:strict', "between:{$min},{$max}"]
                : ['sometimes', 'string', self::decimalRange((string) $min, (string) $max, $exclusive)];
            if (in_array($field, SettingsSchema::NULLABLE_FIELDS, true)) {
                array_splice($rules["changes.{$field}"], 1, 0, ['nullable']);
            }
        }

        // ATL-33 AC2: the schema's 1-4 range is every level the brief defines; this build phase
        // (Phase 0-2) has no Risk Engine or Executor to run autonomy 3-4 safely, so refuse them
        // on top of the schema range rather than widening/narrowing NUMERIC_FIELDS per phase.
        $rules['changes.autonomy_level'][] = self::maxEnabledAutonomyLevel();

        // ATL-113: agent settings. Their keys hold literal dots (`agents.historian.enabled`), so
        // the rule keys escape them; error keys come back unescaped (`changes.agents.historian.enabled`).
        foreach (SettingsSchema::agentFields() as $field => $kind) {
            $key = 'changes.'.self::escape($field);
            $setting = substr($field, (int) strrpos($field, '.') + 1);
            $rules[$key] = match ($kind) {
                'boolean' => ['sometimes', 'boolean:strict'],
                'model' => ['sometimes', 'string', Rule::in(SettingsSchema::AGENT_MODELS)],
                default => ['sometimes', 'integer:strict', 'between:'.implode(',', SettingsSchema::agentIntegerBounds($setting))],
            };
        }
        // ATL-124: markets toggles and lists.
        foreach (SettingsSchema::BOOLEAN_FIELDS as $field) {
            $rules["changes.{$field}"] = ['sometimes', 'boolean:strict'];
        }
        // ATL-141: one value from a fixed list (td_mode_perps).
        foreach (array_keys(SettingsSchema::CHOICE_FIELDS) as $field) {
            $rules["changes.{$field}"] = ['sometimes', 'string', Rule::in(SettingsSchema::choices($field))];
        }
        foreach (SettingsSchema::MARKET_LIST_FIELDS as $field) {
            $rules["changes.{$field}"] = ['sometimes', 'present', 'array', 'list'];
            $rules["changes.{$field}.*"] = ['required', 'string', 'distinct', Rule::in(SettingsSchema::marketKinds())];
        }
        foreach (SettingsSchema::INSTRUMENT_LIST_FIELDS as $field) {
            $rules["changes.{$field}"] = ['sometimes', 'present', 'array', 'list', 'max:'.SettingsSchema::marketListMax()];
            $rules["changes.{$field}.*"] = ['required', 'string', 'max:64', self::instId()];
        }

        foreach (SettingsSchema::WEB_DOMAIN_FIELDS as $field) {
            $key = 'changes.'.self::escape($field);
            $rules[$key] = ['sometimes', 'present', 'array', 'list', 'max:'.SettingsSchema::webDomainsMax()];
            $rules["{$key}.*"] = ['required', 'string', 'max:253', self::hostname()];
        }

        return $rules;
    }

    private function knownFields(): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail): void {
            $raw = $this->input('changes');
            $unknown = is_array($raw) ? array_diff(array_map(strval(...), array_keys($raw)), SettingsSchema::fields()) : [];
            if ($unknown !== []) {
                $fail(__('validation.array', ['attribute' => $attribute]));
            }
        };
    }

    private static function escape(string $field): string
    {
        return str_replace('.', '\\.', $field);
    }

    private static function hostname(): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail): void {
            if (SettingsSchema::normaliseHostname($value) === null) {
                $fail(__('settings.hostname_format'));
            }
        };
    }

    public static function instId(): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail): void {
            if (SettingsSchema::normaliseInstId($value) === null) {
                $fail(__('settings.inst_id_format'));
            }
        };
    }

    private static function maxEnabledAutonomyLevel(): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail): void {
            $max = (int) config('atlas_defaults.autonomy.max_enabled_level');
            if (is_int($value) && $value > $max) {
                $fail(__('settings.autonomy_level_locked', ['max' => $max]));
            }
        };
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
     * @return array<string, bool|int|string|list<string>|null>
     */
    public function changes(): array
    {
        /** @var array<string, bool|int|string|list<string>|null> $changes */
        $changes = $this->validated('changes');

        return $changes;
    }

    public static function decimalRange(string $min, string $max, bool $exclusive): Closure
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
