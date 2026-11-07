<?php

declare(strict_types=1);

namespace App\Support\Proposals;

use App\Models\Proposal;

/**
 * What ProposalApprovalService did with an approve or reject (ATL-215). `error` is a key under
 * proposals.* (null when it worked); `status` is the HTTP status the web API answers with.
 */
final class ApprovalOutcome
{
    /**
     * @param  array<string, mixed>  $execution  trading_core's execute body (approve only)
     */
    public function __construct(
        public readonly bool $ok,
        public readonly ?string $error,
        public readonly int $status,
        public readonly Proposal $proposal,
        public readonly array $execution = [],
    ) {}
}
