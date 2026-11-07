<?php

declare(strict_types=1);

namespace App\Policies;

use App\Models\Proposal;
use App\Models\User;

/**
 * ATL-162: who may read and act on a proposal. Atlas has a single owner (brief §5, multi-user
 * ready): any signed-in user is that owner. Agents never come through here; they write proposals
 * from Python. Whether an action is allowed *now* (status, window, settings version) is checked by
 * the controller and answered 409, not 403.
 */
class ProposalPolicy
{
    public function viewAny(User $user): bool
    {
        return true;
    }

    public function view(User $user, Proposal $proposal): bool
    {
        return true;
    }

    /** Approve, reject, edit and ask the team. */
    public function act(User $user, Proposal $proposal): bool
    {
        return true;
    }
}
