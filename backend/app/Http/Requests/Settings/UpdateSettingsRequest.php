<?php

declare(strict_types=1);

namespace App\Http\Requests\Settings;

use App\Support\Backup\BackupSettings;
use App\Support\Settings\SettingsLabels;
use App\Support\Settings\SettingsSchema;
use Brick\Math\BigDecimal;
use Carbon\CarbonImmutable;
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

        // ATL-33 AC2: the schema's 1-4 range is every level the brief defines; levels above
        // autonomy.max_enabled_level are refused on top of it (4 since ATL-212, decision #254).
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
        // ATL-214: notifications.<event>.<channel> switches (dotted keys, escaped like the agents').
        foreach (SettingsSchema::notificationFields() as $field) {
            $rules['changes.'.self::escape($field)] = ['sometimes', 'boolean:strict'];
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

        // ATL-163: {instId: ISO 8601 time with an offset} for blocklist entries that lift themselves.
        $rules['changes.'.SettingsSchema::BLOCKLIST_UNTIL_FIELD] = ['sometimes', 'present', 'array', 'max:'.SettingsSchema::marketListMax(), self::expiries()];

        foreach (SettingsSchema::WEB_DOMAIN_FIELDS as $field) {
            $key = 'changes.'.self::escape($field);
            $rules[$key] = ['sometimes', 'present', 'array', 'list', 'max:'.SettingsSchema::webDomainsMax()];
            $rules["{$key}.*"] = ['required', 'string', 'max:253', self::hostname()];
        }

        // ATL-231, decision #255 (3): Settings → Backup (dotted keys escaped like the agents').
        $rules['changes.'.self::escape(BackupSettings::ENABLED)] = ['sometimes', 'boolean:strict'];
        $rules['changes.'.self::escape(BackupSettings::FOLDER)] = ['sometimes', 'string', 'max:'.BackupSettings::FOLDER_MAX_LENGTH, self::backupFolder()];
        $rules['changes.'.self::escape(BackupSettings::TIME)] = ['sometimes', 'string', self::backupTime()];
        $rules['changes.'.self::escape(BackupSettings::KEEP)] = ['sometimes', 'integer:strict', 'between:'.implode(',', BackupSettings::keepBounds())];

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

    /** ATL-231: absolute, creatable or existing, writable, and outside the repository. */
    private static function backupFolder(): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail): void {
            $problem = is_string($value) ? BackupSettings::folderProblem($value) : null;
            if ($problem !== null) {
                $fail(__($problem));
            }
        };
    }

    /** ATL-231: HH:MM, 00:00–23:59, Asia/Baghdad. */
    private static function backupTime(): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail): void {
            if (! is_string($value) || preg_match(BackupSettings::TIME_PATTERN, $value) !== 1) {
                $fail(__('settings.backup_time_format'));
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

    /** Every key an instId, every value an ISO 8601 time with an offset that has not passed. */
    private static function expiries(): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail): void {
            if (! is_array($value)) {
                return;
            }
            if ($value !== [] && array_is_list($value)) {
                $fail(__('settings.blocklist_until_format'));

                return;
            }
            foreach ($value as $id => $at) {
                if (SettingsSchema::normaliseInstId((string) $id) === null) {
                    $fail(__('settings.inst_id_format'));

                    return;
                }
                $utc = SettingsSchema::parseExpiry($at);
                if ($utc === null) {
                    $fail(__('settings.blocklist_until_format'));

                    return;
                }
                if (CarbonImmutable::parse($utc)->isPast()) {
                    $fail(__('settings.blocklist_until_past'));

                    return;
                }
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
     * ATL-222: every field's error names it in plain words, never as its raw underscored key
     * (security round 16 / QA on ATL-216). QA round 1: the same holds for the `changes.<field>.*`
     * element rules on list fields (whitelist, blocklist, enabled_markets, agents.web's domain
     * lists) — without an entry for the wildcard key, Laravel falls back to the raw path
     * ("changes.whitelist.0"). The wildcard attribute key keeps every dot literal: the escaped
     * rule key (`self::escape()`) is only for building the rule, and the validator reports the
     * error back against the unescaped path.
     *
     * @return array<string, string>
     */
    public function attributes(): array
    {
        $attributes = [];
        foreach (SettingsSchema::fields() as $field) {
            $attributes["changes.{$field}"] = SettingsLabels::forField($field);
        }
        foreach ($this->listFieldsWithElementRules() as $field) {
            $attributes["changes.{$field}.*"] = SettingsLabels::forListElement($field);
        }

        return $attributes;
    }

    /**
     * Every field validated with a `changes.<field>.*` element rule, per rules() above.
     *
     * @return list<string>
     */
    private function listFieldsWithElementRules(): array
    {
        return [
            ...SettingsSchema::MARKET_LIST_FIELDS,
            ...SettingsSchema::INSTRUMENT_LIST_FIELDS,
            ...SettingsSchema::WEB_DOMAIN_FIELDS,
        ];
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
     * @return array<string, bool|int|string|list<string>|array<string, string>|null>
     */
    public function changes(): array
    {
        /** @var array<string, bool|int|string|list<string>|array<string, string>|null> $changes */
        $changes = $this->validated('changes');

        return $changes;
    }

    public static function decimalRange(string $min, string $max, bool $exclusive): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail) use ($min, $max, $exclusive): void {
            if (! is_string($value) || preg_match(self::DECIMAL_PATTERN, $value) !== 1) {
                $fail(self::decimalFormatMessage($min, $exclusive));

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

    /**
     * ATL-222 (QA on ATL-216): the format rule's message names the field (via :attribute,
     * substituted from `attributes()`) and its lower bound, instead of the old "must be a decimal
     * number written as a string" wording that said nothing about why "-1" was refused. Most
     * decimal fields allow 0 and up with no meaningful upper bound to show here (the exact ceiling
     * still shows once the value parses, in decimal_range_*), so that common case reads as
     * "a positive amount" rather than a redundant number.
     */
    private static function decimalFormatMessage(string $min, bool $exclusive): string
    {
        if ($min === '0' && ! $exclusive) {
            return __('settings.decimal_format_positive');
        }

        return __($exclusive ? 'settings.decimal_format_exclusive' : 'settings.decimal_format_inclusive', ['min' => $min]);
    }
}
