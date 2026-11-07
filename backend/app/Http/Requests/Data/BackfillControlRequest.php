<?php

declare(strict_types=1);

namespace App\Http\Requests\Data;

/**
 * POST /api/data/resync and POST /api/data/pause: {env?, inst_id?}. Without inst_id the action
 * covers every instrument in the env.
 */
class BackfillControlRequest extends DataRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            ...parent::rules(),
            // An OKX instId (BTC-USDT-SWAP) or a bare ccy (BTC) for the rubik datasets.
            'inst_id' => ['sometimes', 'nullable', 'string', 'max:64', 'regex:/^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$/'],
        ];
    }

    public function instId(): ?string
    {
        $value = $this->input('inst_id');

        return is_string($value) && $value !== '' ? $value : null;
    }
}
