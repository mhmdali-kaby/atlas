<?php

declare(strict_types=1);

namespace App\Http\Resources;

use App\Models\Proposal;
use App\Support\Proposals\ProposalView;
use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

/**
 * A proposal for /api/proposals (ATL-162). The list returns ProposalView::summary(); the show
 * endpoint calls detailed() for the version chain, votes, DA review, risk checks and orders.
 *
 * @mixin Proposal
 */
class ProposalResource extends JsonResource
{
    /** Request attribute the list sets so each row does not re-read settings.version. */
    public const SETTINGS_VERSION = 'proposals.settings_version';

    private bool $detailed = false;

    public function detailed(): self
    {
        $this->detailed = true;

        return $this;
    }

    /**
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        /** @var Proposal $proposal */
        $proposal = $this->resource;
        if ($this->detailed) {
            return ProposalView::detail($proposal);
        }
        $version = $request->attributes->get(self::SETTINGS_VERSION);

        return ProposalView::summary($proposal, is_int($version) ? $version : null);
    }
}
