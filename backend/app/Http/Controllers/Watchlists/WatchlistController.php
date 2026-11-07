<?php

declare(strict_types=1);

namespace App\Http\Controllers\Watchlists;

use App\Http\Controllers\Controller;
use App\Http\Requests\Watchlists\EditWatchlistRequest;
use App\Http\Requests\Watchlists\PinWatchlistItemRequest;
use App\Http\Requests\Watchlists\ShowWatchlistRequest;
use App\Http\Resources\WatchlistItemResource;
use App\Http\Resources\WatchlistResource;
use App\Models\User;
use App\Models\Watchlist;
use App\Models\WatchlistItem;
use App\Support\Auditable;
use App\Support\Events\EventBus;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Gate;

/**
 * ATL-103, brief §8 + decision #247 (3): the owner reads the day's watchlist and, until
 * edit_window_ends_at, removes, restores or pins items. Every write is audited and published
 * as `watchlist.updated` on atlas.warroom (brief §3.2 envelope).
 */
class WatchlistController extends Controller
{
    public function show(ShowWatchlistRequest $request): JsonResponse
    {
        $env = $request->env();
        $date = $request->day();

        $watchlist = Watchlist::query()
            ->where('env', $env)
            ->whereDate('date', $date)
            ->orderByDesc('version')
            ->with('items')
            ->first();

        if ($watchlist !== null) {
            Gate::authorize('view', $watchlist);
        }

        return response()->json([
            'env' => $env,
            'date' => $date,
            'timezone' => config('atlas_defaults.timezone.display'),
            'watchlist' => $watchlist === null ? null : WatchlistResource::shape($watchlist),
        ]);
    }

    public function remove(EditWatchlistRequest $request, Watchlist $watchlist, WatchlistItem $item, Auditable $audit, EventBus $bus): JsonResponse
    {
        return $this->edit($request, $watchlist, function (Watchlist $locked) use ($item): array|JsonResponse {
            $item->refresh();
            if ($item->removed_by_owner_at !== null) {
                return $this->conflict('item_already_removed', $locked);
            }
            $before = WatchlistItemResource::shape($item);
            $item->removed_by_owner_at = Carbon::now();
            $item->save();

            return ['removed', $item, $before];
        }, $audit, $bus);
    }

    public function restore(EditWatchlistRequest $request, Watchlist $watchlist, WatchlistItem $item, Auditable $audit, EventBus $bus): JsonResponse
    {
        return $this->edit($request, $watchlist, function (Watchlist $locked) use ($item): array|JsonResponse {
            $item->refresh();
            if ($item->removed_by_owner_at === null) {
                return $this->conflict('item_not_removed', $locked);
            }
            $before = WatchlistItemResource::shape($item);
            // The rank was never given away (pins take max(rank) + 1 over every row, removed or
            // not), so clearing the timestamp puts the item back where it was.
            $item->removed_by_owner_at = null;
            $item->save();

            return ['restored', $item, $before];
        }, $audit, $bus);
    }

    public function pin(PinWatchlistItemRequest $request, Watchlist $watchlist, Auditable $audit, EventBus $bus): JsonResponse
    {
        $instId = $request->instId();

        return $this->edit($request, $watchlist, function (Watchlist $locked) use ($instId): array {
            /** @var WatchlistItem|null $item */
            $item = $locked->items()->where('inst_id', $instId)->first();
            $before = $item === null ? null : WatchlistItemResource::shape($item);

            if ($item === null) {
                $assetClass = DB::table('instruments')
                    ->where('env', $locked->env)
                    ->where('inst_id', $instId)
                    ->value('asset_class');
                $now = Carbon::now();
                $item = $locked->items()->create([
                    'inst_id' => $instId,
                    'rank' => ((int) $locked->items()->max('rank')) + 1,
                    'asset_class' => is_string($assetClass) ? $assetClass : 'other',
                    'bias' => 'neutral',
                    'conviction' => '0',
                    'thesis' => __('watchlists.pinned_thesis'),
                    'catalysts' => [],
                    'key_levels' => [],
                    'invalidation' => '',
                    'pinned_by_owner' => true,
                    'data_as_of' => $now,
                ]);
                // An "empty" watchlist with an item on it is a published one (save_watchlist's rule).
                if ($locked->status === 'empty') {
                    $locked->status = 'published';
                    $locked->save();
                }
            } else {
                $item->pinned_by_owner = true;
                $item->removed_by_owner_at = null;
                $item->save();
            }

            return ['pinned', $item, $before];
        }, $audit, $bus);
    }

    /**
     * Runs one write under a row lock on the watchlist: refuses an older version or a closed
     * window with 409, then audits and publishes what $apply changed.
     *
     * @param  \Closure(Watchlist): (array{0: string, 1: WatchlistItem, 2: array<string, mixed>|null}|JsonResponse)  $apply
     */
    private function edit(EditWatchlistRequest $request, Watchlist $watchlist, \Closure $apply, Auditable $audit, EventBus $bus): JsonResponse
    {
        /** @var User $user */
        $user = $request->user();

        return DB::transaction(function () use ($watchlist, $apply, $audit, $bus, $user): JsonResponse {
            /** @var Watchlist $locked */
            $locked = Watchlist::query()->lockForUpdate()->findOrFail($watchlist->id);

            if (! $locked->editWindowOpen()) {
                return $this->conflict('edit_window_closed', $locked);
            }
            if (! $locked->isCurrentVersion()) {
                return $this->conflict('not_current_version', $locked);
            }

            $result = $apply($locked);
            if ($result instanceof JsonResponse) {
                return $result;
            }
            [$action, $item, $before] = $result;
            $item->refresh();
            $after = WatchlistItemResource::shape($item);

            $audit->record("watchlist.item_{$action}", 'watchlist_items', $item->id, $before, [
                ...$after,
                'watchlist_id' => $locked->id,
                'env' => $locked->env,
                'date' => $locked->date->toDateString(),
                'version' => $locked->version,
            ], $user);

            $locked->load('items');
            $shape = WatchlistResource::shape($locked);

            // Inside the transaction, like owner War Room posts: if Redis is down the write fails.
            $bus->publish(
                stream: 'atlas.warroom',
                type: 'watchlist.updated',
                env: $locked->env,
                source: 'human:owner',
                payload: [
                    'action' => $action,
                    'watchlist_id' => $locked->id,
                    'date' => $shape['date'],
                    'version' => $locked->version,
                    'status' => $locked->status,
                    'edit_window_ends_at' => $shape['edit_window_ends_at'],
                    'item' => $after,
                    'items' => $shape['items'],
                ],
            );

            return response()->json(['watchlist' => $shape, 'item' => $after]);
        });
    }

    private function conflict(string $error, Watchlist $watchlist): JsonResponse
    {
        return response()->json([
            'ok' => false,
            'error' => $error,
            'message' => __("watchlists.{$error}"),
            'edit_window_ends_at' => $watchlist->editWindowEndsAt()->utc()->toIso8601ZuluString('microsecond'),
        ], 409);
    }
}
