<?php

declare(strict_types=1);

namespace App\Policies;

use App\Models\User;
use App\Models\Watchlist;

/**
 * ATL-103: who may read and edit a watchlist. Atlas has a single owner (brief §5, multi-user
 * ready): any signed-in user is that owner. Agents never come through here; they write
 * watchlists from Python.
 */
class WatchlistPolicy
{
    public function view(User $user, Watchlist $watchlist): bool
    {
        return true;
    }

    /** Pin, remove and restore. The edit window is checked by the controller (409, not 403). */
    public function update(User $user, Watchlist $watchlist): bool
    {
        return true;
    }
}
