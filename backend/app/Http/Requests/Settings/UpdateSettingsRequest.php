<?php

declare(strict_types=1);

namespace App\Http\Requests\Settings;

use App\Models\Setting;
use App\Support\Agents\PromptVersions;
use App\Support\Backup\BackupSettings;
use App\Support\Settings\SettingsLabels;
use App\Support\Settings\SettingsSchema;
use App\Support\Settings\SettingsService;
use App\Support\System\FirstLiveMonth;
use Brick\Math\BigDecimal;
use Carbon\CarbonImmutable;
use Closure;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;
use Illuminate\Validation\Validator;

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

        // ATL-238, decision #255 (6): during the first live month, live autonomy stays at or below
        // max_live_autonomy and the live bot capital cap cannot be cleared.
        if ($this->input('env') === 'live' && FirstLiveMonth::active()) {
            $rules['changes.autonomy_level'][] = self::firstLiveMonthAutonomy();
        }

        // ATL-113: agent settings. Their keys hold literal dots (`agents.historian.enabled`), so
        // the rule keys escape them; error keys come back unescaped (`changes.agents.historian.enabled`).
        foreach (SettingsSchema::agentFields() as $field => $kind) {
            $key = 'changes.'.self::escape($field);
            $setting = substr($field, (int) strrpos($field, '.') + 1);
            $rules[$key] = match ($kind) {
                'boolean' => ['sometimes', 'boolean:strict'],
                'model' => ['sometimes', 'string', Rule::in(SettingsSchema::AGENT_MODELS)],
                // ATL-251: the pin endpoint checks the version belongs to the agent; here it must
                // at least be a stored version of that agent.
                'prompt_version' => ['sometimes', 'nullable', 'integer:strict', 'min:1', 'max:'.PromptVersions::MAX_VERSION, self::storedPromptVersion(explode('.', $field)[1])],
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

        // ATL-228, decision #255 (2): Settings → Schedule (Asia/Baghdad times; dotted keys escaped).
        foreach (array_keys(SettingsSchema::SCHEDULE_TIME_FIELDS) as $field) {
            $rules['changes.'.self::escape($field)] = ['sometimes', 'string', self::pattern(SettingsSchema::TIME_PATTERN, 'settings.schedule_time_format')];
        }
        foreach (array_keys(SettingsSchema::SCHEDULE_WEEKLY_FIELDS) as $field) {
            $rules['changes.'.self::escape($field)] = ['sometimes', 'string', self::pattern(SettingsSchema::WEEKLY_PATTERN, 'settings.schedule_weekly_format')];
        }
        $rules['changes.'.self::escape(SettingsSchema::SCHEDULE_INTERVAL_FIELD)] = ['sometimes', 'integer:strict', 'between:'.implode(',', SettingsSchema::scheduleIntervalBounds())];
        $rules['changes.'.self::escape(SettingsSchema::SCHEDULE_QUIET_ENABLED_FIELD)] = ['sometimes', 'boolean:strict'];
        $rules['changes.'.self::escape(SettingsSchema::SCHEDULE_WEEKEND_FIELD)] = ['sometimes', 'string', Rule::in(SettingsSchema::weekendChoices())];

        return $rules;
    }

    /**
     * ATL-228: the schedule's cross-field rules, checked on the stored values with the changes on
     * top: the first analysis slot is before the last, Scout's watchlist is after both 07:30 jobs
     * (it reads their output), and quiet hours are not an empty window. Quiet hours may cross
     * midnight. The error goes on the field the request changed (the later one when both did).
     *
     * @return list<Closure(Validator): void>
     */
    public function after(): array
    {
        return [function (Validator $validator): void {
            // ATL-238: a `nullable` field skips its closures on null, so clearing the live cap
            // (null, or an empty string) is refused here rather than in rules().
            $raw = $this->input('changes');
            if ($this->input('env') === 'live' && is_array($raw) && array_key_exists('bot_capital_cap_usdt', $raw)
                && ($raw['bot_capital_cap_usdt'] === null || $raw['bot_capital_cap_usdt'] === '') && FirstLiveMonth::active()) {
                $validator->errors()->add('changes.bot_capital_cap_usdt', __('settings.first_live_month_cap_required', ['until' => self::untilLabel()]));
            }
        }, function (Validator $validator): void {
            $raw = $this->input('changes');
            if (! is_array($raw) || array_intersect(array_keys($raw), SettingsSchema::scheduleFields()) === []) {
                return;
            }
            foreach (SettingsSchema::scheduleFields() as $field) {
                if ($validator->errors()->has("changes.{$field}")) {
                    return;
                }
            }
            $merged = [...$this->storedSchedule(), ...array_intersect_key($raw, array_flip(SettingsSchema::scheduleFields()))];
            $time = fn (string $field): string => is_string($merged[$field] ?? null) ? $merged[$field] : '';

            $order = [
                ['schedule.analysis_first_slot', 'schedule.analysis_last_slot', 'settings.schedule_first_before_last'],
                ['schedule.news_morning_brief', 'schedule.scout_watchlist', 'settings.schedule_scout_after_brief'],
                ['schedule.historian_regime_table', 'schedule.scout_watchlist', 'settings.schedule_scout_after_regime'],
            ];
            foreach ($order as [$earlier, $later, $message]) {
                if ($time($earlier) < $time($later)) {
                    continue;
                }
                $field = array_key_exists($later, $raw) || ! array_key_exists($earlier, $raw) ? $later : $earlier;
                $validator->errors()->add("changes.{$field}", __($message, [
                    'earlier' => SettingsLabels::forField($earlier),
                    'later' => SettingsLabels::forField($later),
                ]));
            }

            if ($time('schedule.quiet_hours_start') === $time('schedule.quiet_hours_end')) {
                $field = array_key_exists('schedule.quiet_hours_end', $raw) ? 'schedule.quiet_hours_end' : 'schedule.quiet_hours_start';
                $validator->errors()->add("changes.{$field}", __('settings.schedule_quiet_empty'));
            }
        }];
    }

    /**
     * The env's stored schedule values (defaults for any field added since, or with no row yet).
     * Read without creating the row: validation must not write.
     *
     * @return array<string, mixed>
     */
    private function storedSchedule(): array
    {
        $env = $this->input('env');
        $setting = is_string($env) ? Setting::query()->where('env', $env)->first() : null;
        $values = $setting !== null ? app(SettingsService::class)->values($setting) : SettingsSchema::defaults();

        return array_intersect_key($values, array_flip(SettingsSchema::scheduleFields()));
    }

    private static function pattern(string $pattern, string $message): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail) use ($pattern, $message): void {
            if (! is_string($value) || preg_match($pattern, $value) !== 1) {
                $fail(__($message));
            }
        };
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

    private static function firstLiveMonthAutonomy(): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail): void {
            $max = FirstLiveMonth::maxAutonomy();
            if (is_int($value) && $value > $max) {
                $fail(__('settings.first_live_month_autonomy', [
                    'max' => $max,
                    'until' => self::untilLabel(),
                ]));
            }
        };
    }

    /** The end of the first live month in the owner's display zone, e.g. "2026-12-02 10:12". */
    private static function untilLabel(): string
    {
        $zone = config('atlas_defaults.timezone.display');

        return FirstLiveMonth::endsAt()?->setTimezone(is_string($zone) ? $zone : 'UTC')->format('Y-m-d H:i') ?? '';
    }

    /** ATL-251: `agents.<slug>.prompt_version` must name a prompt_versions row of that agent. */
    private static function storedPromptVersion(string $slug): Closure
    {
        return function (string $attribute, mixed $value, Closure $fail) use ($slug): void {
            if (is_int($value) && ! PromptVersions::exists($slug, $value)) {
                $fail(__('settings.prompt_version_unknown', ['version' => $value]));
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
