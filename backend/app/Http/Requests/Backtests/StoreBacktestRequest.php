<?php

declare(strict_types=1);

namespace App\Http\Requests\Backtests;

use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Validator;

/**
 * POST /api/backtests (ATL-208): the same body trading_core's POST /internal/backtest accepts
 * (docs/handoff/ATL-205-backtest-endpoint.md; atlas_quant.RuleSpec + BacktestRequest), checked here
 * so an invalid rule is a 422 before anything is queued. trading_core re-validates every field.
 */
class StoreBacktestRequest extends FormRequest
{
    private const INST_ID = 'regex:/^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$/';

    /** An indicator name: an identifier, so it can never be mistaken for a number. */
    private const NAME = '/^[A-Za-z_][A-Za-z0-9_]{0,63}$/';

    private const TOP_LEVEL = ['rule_spec', 'inst_ids', 'bar', 'period', 'splits', 'slippage_bps'];

    private const RULE_KEYS = ['name', 'side', 'indicators', 'entry', 'exit', 'stop_pct', 'take_profit_pct'];

    /** The price series a condition may name besides an indicator (atlas_quant CLOSE). */
    private const CLOSE = 'close';

    public function authorize(): bool
    {
        return true;
    }

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        $maxInst = config('atlas_defaults.backtest.max_inst_ids');
        $maxInst = is_int($maxInst) ? $maxInst : 5;

        return [
            'rule_spec' => ['required', 'array:'.implode(',', self::RULE_KEYS)],
            'rule_spec.name' => ['sometimes', 'string', 'min:1', 'max:120'],
            'rule_spec.side' => ['sometimes', 'string', 'in:long,short'],
            'rule_spec.indicators' => ['sometimes', 'array', 'max:20'],
            'rule_spec.indicators.*' => ['required', 'array:kind,window'],
            'rule_spec.indicators.*.kind' => ['required', 'string', 'in:sma,ema,rsi'],
            'rule_spec.indicators.*.window' => ['required', 'integer', 'min:1', 'max:5000'],
            'rule_spec.entry' => ['required', 'array', 'list', 'min:1', 'max:10'],
            'rule_spec.exit' => ['sometimes', 'array', 'list', 'max:10'],
            'rule_spec.entry.*' => ['required', 'array:left,op,right'],
            'rule_spec.exit.*' => ['required', 'array:left,op,right'],
            'rule_spec.entry.*.left' => ['required', 'string', 'max:64'],
            'rule_spec.exit.*.left' => ['required', 'string', 'max:64'],
            'rule_spec.entry.*.op' => ['required', 'string', 'in:crosses_above,crosses_below,above,below'],
            'rule_spec.exit.*.op' => ['required', 'string', 'in:crosses_above,crosses_below,above,below'],
            'rule_spec.entry.*.right' => ['required'],
            'rule_spec.exit.*.right' => ['required'],
            'rule_spec.stop_pct' => ['nullable', 'numeric', 'gt:0', 'lt:100'],
            'rule_spec.take_profit_pct' => ['nullable', 'numeric', 'gt:0'],
            'inst_ids' => ['required', 'array', 'list', 'min:1', 'max:'.$maxInst],
            'inst_ids.*' => ['required', 'string', 'max:64', self::INST_ID, 'distinct'],
            'bar' => ['required', 'string', 'in:1m,5m,15m,1H,4H,1D'],
            'period' => ['required', 'array:start,end'],
            'period.start' => ['required', 'string', 'date', self::AWARE],
            'period.end' => ['required', 'string', 'date', self::AWARE],
            'splits' => ['nullable', 'integer', 'min:1', 'max:10'],
            'slippage_bps' => ['nullable', 'numeric', 'min:0', 'max:1000'],
        ];
    }

    /** An ISO datetime with an explicit offset (pydantic AwareDatetime). */
    private const AWARE = 'regex:/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/';

    /**
     * Cross-field rules RuleSpec and BacktestRequest enforce: known top-level keys only, every
     * condition names a defined indicator or `close`, `close` is not an indicator name, a numeric
     * `right` is a level, and period.start < period.end.
     *
     * @return list<\Closure(Validator): void>
     */
    public function after(): array
    {
        return [function (Validator $validator): void {
            if ($validator->errors()->isNotEmpty()) {
                return;
            }
            $data = $this->all();

            foreach (array_diff(array_keys($data), self::TOP_LEVEL) as $key) {
                $validator->errors()->add((string) $key, __('backtests.unknown_field', ['field' => (string) $key]));
            }

            /** @var array<string, mixed> $rule */
            $rule = $data['rule_spec'];
            $indicators = is_array($rule['indicators'] ?? null) ? $rule['indicators'] : [];
            foreach (array_keys($indicators) as $name) {
                $name = (string) $name;
                if ($name === self::CLOSE) {
                    $validator->errors()->add('rule_spec.indicators', __('backtests.close_reserved'));
                } elseif (preg_match(self::NAME, $name) !== 1) {
                    $validator->errors()->add('rule_spec.indicators', __('backtests.bad_indicator_name', ['name' => $name]));
                }
            }
            $known = array_merge(array_map('strval', array_keys($indicators)), [self::CLOSE]);

            foreach (['entry', 'exit'] as $side) {
                $conditions = is_array($rule[$side] ?? null) ? $rule[$side] : [];
                foreach ($conditions as $i => $cond) {
                    if (! is_array($cond)) {
                        continue;
                    }
                    if (! in_array($cond['left'], $known, true)) {
                        $validator->errors()->add("rule_spec.{$side}.{$i}.left", __('backtests.unknown_indicator', ['name' => (string) $cond['left']]));
                    }
                    $right = $cond['right'];
                    if (is_string($right)) {
                        if (! in_array($right, $known, true)) {
                            $validator->errors()->add("rule_spec.{$side}.{$i}.right", __('backtests.unknown_indicator', ['name' => $right]));
                        }
                    } elseif (! is_int($right) && ! is_float($right)) {
                        $validator->errors()->add("rule_spec.{$side}.{$i}.right", __('backtests.bad_right'));
                    }
                }
            }

            /** @var array{start: string, end: string} $period */
            $period = $data['period'];
            if (strtotime($period['start']) >= strtotime($period['end'])) {
                $validator->errors()->add('period.end', __('backtests.period_order'));
            }
        }];
    }

    /**
     * The body exactly as trading_core's BacktestRequest expects it: only known keys, nulls dropped
     * at the top level (rule_spec keeps its nulls, which RuleSpec accepts).
     *
     * @return array<string, mixed>
     */
    public function backtest(): array
    {
        $data = $this->all();
        $out = [];
        foreach (self::TOP_LEVEL as $key) {
            if (array_key_exists($key, $data) && $data[$key] !== null) {
                $out[$key] = $data[$key];
            }
        }
        $rule = is_array($data['rule_spec'] ?? null) ? $data['rule_spec'] : [];
        $rule = array_intersect_key($rule, array_flip(self::RULE_KEYS));
        // An empty JSON object decodes to [] and would re-encode as a list; RuleSpec defaults it.
        if (($rule['indicators'] ?? null) === []) {
            unset($rule['indicators']);
        }
        $out['rule_spec'] = $rule;

        return $out;
    }
}
