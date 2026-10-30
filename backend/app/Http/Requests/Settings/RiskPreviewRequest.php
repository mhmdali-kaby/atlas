<?php

declare(strict_types=1);

namespace App\Http\Requests\Settings;

use App\Support\Settings\SettingsSchema;
use Brick\Math\BigDecimal;
use Closure;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * GET /api/settings/risk-preview: ?env=demo|live, plus an optional draft value for any field the
 * preview's formula reads, so the Settings page can preview an unsaved edit without writing a
 * version (brief §14.4). A draft overrides the saved value for this one computation only.
 */
class RiskPreviewRequest extends FormRequest
{
    private const DECIMAL_PATTERN = '/^\d{1,15}(\.\d{1,8})?$/';

    /** The settings fields the preview's formula reads; the only ones a draft may override. */
    private const DECIMAL_DRAFT_FIELDS = [
        'max_loss_per_trade_pct',
        'max_position_pct',
        'max_usdt_per_trade',
        'max_new_exposure_per_day_usdt',
    ];

    /** Every field a draft may carry, decimals plus the nullable cap and the profile. */
    public const DRAFT_FIELDS = [
        'risk_profile',
        'bot_capital_cap_usdt',
        ...self::DECIMAL_DRAFT_FIELDS,
    ];

    public function authorize(): bool
    {
        return true;
    }

    /**
     * Query strings cannot carry a JSON null. An empty string or the literal "null" (either case)
     * means the same thing a PATCH null does for bot_capital_cap_usdt (decision #250: no cap).
     */
    protected function prepareForValidation(): void
    {
        if (! $this->has('bot_capital_cap_usdt')) {
            return;
        }
        $value = $this->query('bot_capital_cap_usdt');
        if (is_string($value) && in_array(strtolower(trim($value)), ['', 'null'], true)) {
            $this->merge(['bot_capital_cap_usdt' => null]);
        }
    }

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        $rules = [
            'env' => ['sometimes', 'string', Rule::in(SettingsEnvRequest::envs())],
            'risk_profile' => ['sometimes', 'string', Rule::in(SettingsSchema::PROFILES)],
        ];

        foreach (self::DECIMAL_DRAFT_FIELDS as $field) {
            [, $min, $max, $exclusive] = SettingsSchema::NUMERIC_FIELDS[$field];
            $rules[$field] = ['sometimes', 'string', self::decimalRange((string) $min, (string) $max, $exclusive)];
        }

        // bot_capital_cap_usdt is the one nullable field (decision #250: null = no cap).
        [, $min, $max, $exclusive] = SettingsSchema::NUMERIC_FIELDS['bot_capital_cap_usdt'];
        $rules['bot_capital_cap_usdt'] = ['sometimes', 'nullable', 'string', self::decimalRange((string) $min, (string) $max, $exclusive)];

        return $rules;
    }

    public function env(): string
    {
        $env = $this->validated('env') ?? config('atlas_defaults.env.default');

        return is_string($env) ? $env : 'demo';
    }

    /**
     * Only the fields the request actually carries, so the caller can tell "no draft for this
     * field" (fall back to the saved settings) from "drafted to null" (bot_capital_cap_usdt only).
     *
     * @return array<string, string|null>
     */
    public function drafts(): array
    {
        $drafts = [];
        foreach (self::DRAFT_FIELDS as $field) {
            if (! $this->has($field)) {
                continue;
            }
            $value = $this->validated($field);
            $drafts[$field] = is_string($value) ? $value : null;
        }

        return $drafts;
    }

    private static function decimalRange(string $min, string $max, bool $exclusive): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail) use ($min, $max, $exclusive): void {
            if ($value === null) {
                return;
            }
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
