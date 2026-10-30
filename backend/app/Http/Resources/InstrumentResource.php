<?php

declare(strict_types=1);

namespace App\Http\Resources;

use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * One instruments row from GET /api/instruments (ATL-119, brief §16). `blocked` is set by the
 * controller before wrapping: true when the instrument's inst_id is on the Settings blocklist,
 * so the owner's pin search (ATL-115) can show it disabled rather than hide it.
 */
class InstrumentResource extends JsonResource
{
    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var object $row */
        $row = $this->resource;
        $data = (array) $row;

        return [
            'inst_id' => $data['inst_id'],
            'inst_type' => $data['inst_type'],
            'asset_class' => $data['asset_class'],
            'base_ccy' => $data['base_ccy'],
            'quote_ccy' => $data['quote_ccy'],
            'settle_ccy' => $data['settle_ccy'],
            'blocked' => (bool) $data['blocked'],
        ];
    }
}
