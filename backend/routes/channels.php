<?php

use App\Models\User;
use Illuminate\Support\Facades\Broadcast;

Broadcast::channel('App.Models.User.{id}', function ($user, $id) {
    return (int) $user->id === (int) $id;
});

// Brief §3.2 browser channels fed by `php artisan atlas:bridge`. Atlas has a single owner, so any
// signed-in user may listen; guests are refused (403) before these callbacks run.
// private-market.{instId} arrives with the market-data patch.
foreach (['warroom', 'account', 'system'] as $channel) {
    Broadcast::channel($channel, fn (User $user): bool => true);
}
