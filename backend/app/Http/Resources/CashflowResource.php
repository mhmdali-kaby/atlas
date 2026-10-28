<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Support\Portfolio\PortfolioService;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * One deposits_withdrawals row (a stdClass from the query builder).
 */
class CashflowResource extends JsonResource
{
    /**
     * @return array<string, string|null>
     */
    public function toArray(Request $request): array
    {
        $row = $this->resource;

        return app(PortfolioService::class)->cashflow(is_object($row) ? $row : (object) []);
    }
}
