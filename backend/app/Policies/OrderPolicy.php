<?php

declare(strict_types=1);

namespace App\Policies;

use App\Models\Order;
use App\Models\User;

/**
 * ATL-148: who may cancel an order. Atlas has a single owner (brief §5, multi-user ready): any
 * signed-in user is that owner. The env scope (only the active env's orders) is the controller's
 * lookup, answered 404; whether the order is still open is trading_core's, answered 409.
 */
class OrderPolicy
{
    public function cancel(User $user, Order $order): bool
    {
        return true;
    }
}
