<?php

declare(strict_types=1);

namespace App\Http\Controllers\Notifications;

use App\Http\Controllers\Controller;
use App\Http\Requests\Notifications\ListNotificationsRequest;
use App\Http\Resources\NotificationResource;
use App\Models\Notification;
use App\Support\Auditable;
use App\Support\System\ActiveEnv;
use Illuminate\Database\Query\Builder;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;

/**
 * Brief §17 notifications of the active env (ATL-214): list newest first, mark one or all read.
 * Rows are written only by atlas:bridge (App\Support\Notifications\Notifier); every write here
 * (read_at) is audited.
 */
class NotificationController extends Controller
{
    public function index(ListNotificationsRequest $request): JsonResponse
    {
        $env = ActiveEnv::get();
        // The query builder, not Eloquent: its cursor carries the raw timestamptz text that the
        // shared (created_at, id) cursor check accepts.
        $page = DB::table('notifications')
            ->where('env', $env)
            ->when($request->unreadOnly(), fn (Builder $q) => $q->whereNull('read_at'))
            ->orderByDesc('created_at')
            ->orderByDesc('id')
            ->cursorPaginate($request->limit())
            ->withQueryString();

        return response()->json([
            'data' => NotificationResource::collection(
                Notification::hydrate(array_map(fn (object $row): array => (array) $row, $page->items()))
            )->resolve($request),
            'meta' => [
                'env' => $env,
                'per_page' => $page->perPage(),
                'unread_count' => Notification::query()->where('env', $env)->whereNull('read_at')->count(),
                'next_cursor' => $page->nextCursor()?->encode(),
                'prev_cursor' => $page->previousCursor()?->encode(),
            ],
        ]);
    }

    public function read(Request $request, string $notification, Auditable $audit): JsonResponse
    {
        $row = DB::transaction(function () use ($notification, $audit): Notification {
            $row = Notification::query()
                ->where('env', ActiveEnv::get())
                ->whereKey($notification)
                ->lockForUpdate()
                ->first();
            abort_if($row === null, 404);

            $before = $row->read_at;
            if ($before === null) {
                $row->read_at = Carbon::now();
                $row->save();
            }
            $audit->record('notification.read', 'notifications', $row->id, [
                'read_at' => $before?->utc()->toIso8601ZuluString('microsecond'),
            ], [
                'read_at' => $row->read_at->utc()->toIso8601ZuluString('microsecond'),
            ]);

            return $row;
        });

        return response()->json(['data' => (new NotificationResource($row->refresh()))->resolve($request)]);
    }

    public function readAll(Request $request, Auditable $audit): JsonResponse
    {
        $env = ActiveEnv::get();
        $count = DB::transaction(function () use ($env, $audit): int {
            $at = Carbon::now();
            $count = Notification::query()
                ->where('env', $env)
                ->whereNull('read_at')
                ->update(['read_at' => $at->utc()->format('Y-m-d H:i:s.uP')]);
            $audit->record('notification.read_all', 'notifications', null, null, [
                'env' => $env,
                'marked' => $count,
                'read_at' => $at->utc()->toIso8601ZuluString('microsecond'),
            ]);

            return $count;
        });

        return response()->json(['data' => ['env' => $env, 'marked' => $count, 'unread_count' => 0]]);
    }
}
