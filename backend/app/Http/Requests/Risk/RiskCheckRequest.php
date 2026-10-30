<?php

declare(strict_types=1);

namespace App\Http\Requests\Risk;

use Illuminate\Foundation\Http\FormRequest;

/**
 * POST /api/risk/check: a hand-written TradeProposal (brief §6.3; atlas_shared.proposals). Every
 * order needs a stop (rule 3), so a missing stop is a 422 here before anything reaches trading_core.
 * Prices and percentages may be JSON numbers or decimal strings; trading_core parses them as Decimal
 * and re-checks the cross-field rules (stop side, entry for limit orders, take-profit total).
 */
class RiskCheckRequest extends FormRequest
{
    private const INST_ID = 'regex:/^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$/';

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
            'inst_id' => ['required', 'string', 'max:64', self::INST_ID],
            'side' => ['required', 'string', 'in:buy,sell'],
            'order_type' => ['required', 'string', 'in:market,limit,post_only'],
            'entry' => ['nullable', 'numeric', 'gt:0'],
            'entry_zone' => ['nullable', 'array', 'size:2'],
            'entry_zone.*' => ['required', 'numeric', 'gt:0'],
            'stop' => ['required', 'numeric', 'gt:0'],
            'take_profits' => ['required', 'array', 'min:1', 'max:3'],
            'take_profits.*' => ['required', 'array:price,pct'],
            'take_profits.*.price' => ['required', 'numeric', 'gt:0'],
            'take_profits.*.pct' => ['required', 'numeric', 'gt:0', 'lte:100'],
            'time_stop_minutes' => ['nullable', 'integer', 'min:1'],
            'size_suggestion_pct_equity' => ['required', 'numeric', 'gt:0', 'lte:100'],
            'leverage_suggestion' => ['required', 'integer', 'min:1'],
            'expected_r_multiple' => ['required', 'numeric'],
            'probability_estimate' => ['required', 'numeric', 'between:0,1'],
            'rationale_md' => ['required', 'string', 'max:8000'],
            'invalidation' => ['required', 'string', 'max:2000'],
            'evidence' => ['sometimes', 'array', 'max:50'],
            'evidence.*' => ['required', 'array:type,id'],
            'evidence.*.type' => ['required', 'string', 'max:64'],
            'evidence.*.id' => ['required', 'string', 'max:200'],
        ];
    }

    /**
     * The proposal exactly as trading_core's TradeProposal expects it: only known keys, nulls dropped.
     *
     * @return array<string, mixed>
     */
    public function proposal(): array
    {
        $keys = array_keys(array_filter($this->rules(), static fn (string $k): bool => ! str_contains($k, '.'), ARRAY_FILTER_USE_KEY));

        return array_filter(
            $this->safe()->only($keys),
            static fn (mixed $v): bool => $v !== null,
        );
    }
}
