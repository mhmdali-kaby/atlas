<?php

declare(strict_types=1);

namespace App\Http\Requests\Settings;

use App\Support\Settings\SettingsSchema;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * GET /api/settings/markets/eligibility: env, inst_ids[] (1..max_inst_ids) and optional draft[...]
 * values of the Markets settings, checked like PATCH /api/settings checks them. A draft value
 * replaces the saved one for this answer only. A list sent as an empty string (`draft[blocklist]=`)
 * is the empty list, because a query string cannot carry `[]`.
 */
class MarketsEligibilityRequest extends FormRequest
{
    /** Settings keys a draft may override. */
    public const DRAFT_LIST_FIELDS = ['enabled_markets', 'whitelist', 'blocklist'];

    public const DRAFT_DECIMAL_FIELDS = [
        'max_spread_bps',
        'min_24h_volume_usd_spot',
        'min_24h_volume_usd_crypto_perp',
        'min_24h_volume_usd_stock_perp',
        'min_24h_volume_usd_commodity_perp',
    ];

    public const MAX_INST_IDS = 50;

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
            'env' => ['sometimes', 'string', Rule::in(SettingsEnvRequest::envs())],
            'inst_ids' => ['required', 'array', 'list', 'min:1', 'max:'.self::MAX_INST_IDS],
            'inst_ids.*' => ['required', 'string', 'max:64', UpdateSettingsRequest::instId()],
            'draft' => ['sometimes', 'array:'.implode(',', [...self::DRAFT_LIST_FIELDS, ...self::DRAFT_DECIMAL_FIELDS])],
            'draft.enabled_markets' => ['sometimes', 'nullable', 'array', 'list'],
            'draft.enabled_markets.*' => ['required', 'string', 'distinct', Rule::in(SettingsSchema::marketKinds())],
        ];
        foreach (['whitelist', 'blocklist'] as $field) {
            $rules["draft.{$field}"] = ['sometimes', 'nullable', 'array', 'list', 'max:'.SettingsSchema::marketListMax()];
            $rules["draft.{$field}.*"] = ['required', 'string', 'max:64', UpdateSettingsRequest::instId()];
        }
        foreach (self::DRAFT_DECIMAL_FIELDS as $field) {
            [, $min, $max, $exclusive] = SettingsSchema::NUMERIC_FIELDS[$field];
            $rules["draft.{$field}"] = ['sometimes', 'string', UpdateSettingsRequest::decimalRange((string) $min, (string) $max, $exclusive)];
        }

        return $rules;
    }

    public function env(): string
    {
        $env = $this->validated('env') ?? config('atlas_defaults.env.default');

        return is_string($env) ? $env : 'demo';
    }

    /**
     * Requested instIds in stored form (trimmed, upper-case), first occurrence order, no repeats.
     *
     * @return list<string>
     */
    public function instIds(): array
    {
        $ids = [];
        foreach ((array) $this->validated('inst_ids') as $raw) {
            $id = SettingsSchema::normaliseInstId($raw);
            if ($id !== null && ! in_array($id, $ids, true)) {
                $ids[] = $id;
            }
        }

        return $ids;
    }

    /**
     * Draft values in canonical settings form (SettingsSchema::normalise).
     *
     * @return array<string, bool|int|string|list<string>|null>
     */
    public function draft(): array
    {
        $draft = $this->validated('draft');
        if (! is_array($draft)) {
            return [];
        }
        foreach (self::DRAFT_LIST_FIELDS as $field) {
            if (array_key_exists($field, $draft) && $draft[$field] === null) {
                $draft[$field] = [];
            }
        }

        return SettingsSchema::normalise($draft);
    }
}
