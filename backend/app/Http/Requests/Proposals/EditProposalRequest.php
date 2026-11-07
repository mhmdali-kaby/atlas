<?php

declare(strict_types=1);

namespace App\Http\Requests\Proposals;

use App\Models\Proposal;
use App\Support\Proposals\Dec;
use App\Support\Proposals\ProposalRules;
use Brick\Math\BigDecimal;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Validator;

/**
 * PATCH /api/proposals/{id} — the owner's Edit (brief §12): any of
 *
 *   { "size": "2.5", "stop": "3975", "targets": [{"price": "4100", "pct": "50"}, …] }
 *
 * `size` is the size suggestion in % of equity (the Risk Engine still decides the final size);
 * prices are decimal strings or JSON numbers. The stop may only tighten (ProposalRules).
 */
class EditProposalRequest extends FormRequest
{
    private const DECIMAL = 'regex:/^\d{1,20}(\.\d{1,18})?$/';

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
            'size' => ['nullable', 'required_without_all:stop,targets', 'numeric', 'gt:0', 'lte:100', self::DECIMAL],
            'stop' => ['nullable', 'required_without_all:size,targets', 'numeric', 'gt:0', self::DECIMAL],
            'targets' => ['nullable', 'required_without_all:size,stop', 'array', 'min:1', 'max:3'],
            'targets.*' => ['array:price,pct'],
            'targets.*.price' => ['required', 'numeric', 'gt:0', self::DECIMAL],
            'targets.*.pct' => ['required', 'numeric', 'gt:0', 'lte:100', self::DECIMAL],
        ];
    }

    /**
     * @return list<\Closure(Validator): void>
     */
    public function after(): array
    {
        return [function (Validator $validator): void {
            if ($validator->errors()->isNotEmpty()) {
                return;
            }
            $proposal = $this->proposal();
            if ($proposal->kind !== 'entry') {
                return; // the controller answers 409 not_editable
            }
            $stop = $this->stop() ?? Dec::of($proposal->stop);
            if ($stop === null) {
                return;
            }
            $newStop = $this->stop();
            if ($newStop !== null && ($code = ProposalRules::stopError($proposal, $newStop)) !== null) {
                $validator->errors()->add('stop', __("proposals.{$code}"));

                return;
            }
            $targets = $this->targets();
            if ($targets !== null && ($code = ProposalRules::targetsError($proposal, $targets, $stop)) !== null) {
                $validator->errors()->add('targets', __("proposals.{$code}"));
            }
        }];
    }

    public function proposal(): Proposal
    {
        $proposal = $this->route('proposal');
        if (! $proposal instanceof Proposal) {
            abort(404);
        }

        return $proposal;
    }

    public function size(): ?BigDecimal
    {
        return Dec::of($this->input('size'));
    }

    public function stop(): ?BigDecimal
    {
        return Dec::of($this->input('stop'));
    }

    /**
     * @return list<array{price: BigDecimal, pct: BigDecimal}>|null
     */
    public function targets(): ?array
    {
        $raw = $this->input('targets');
        if (! is_array($raw)) {
            return null;
        }
        $out = [];
        foreach ($raw as $tp) {
            $price = is_array($tp) ? Dec::of($tp['price'] ?? null) : null;
            $pct = is_array($tp) ? Dec::of($tp['pct'] ?? null) : null;
            if ($price !== null && $pct !== null) {
                $out[] = ['price' => $price, 'pct' => $pct];
            }
        }

        return $out;
    }
}
