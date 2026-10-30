<?php

declare(strict_types=1);

namespace App\Http\Controllers\Portfolio;

use App\Http\Controllers\Controller;
use App\Http\Requests\Portfolio\ListOrdersRequest;
use App\Http\Resources\AlgoOrderResource;
use App\Http\Resources\OrderResource;
use App\Models\AlgoOrder;
use App\Models\Order;
use App\Support\System\ActiveEnv;
use Illuminate\Http\JsonResponse;

/**
 * GET /api/portfolio/orders (brief §13 Orders, ATL-148): open orders and pending algo orders of the
 * active env, newest first, from Postgres (trading_core keeps them in sync with OKX).
 */
class OrdersController extends Controller
{
    private const LIMIT = 500;

    public function index(ListOrdersRequest $request): JsonResponse
    {
        $env = ActiveEnv::get();
        $orders = $request->wants('open')
            ? Order::query()->where('env', $env)->whereIn('state', Order::OPEN_STATES)
                ->orderByDesc('created_at')->orderByDesc('id')->limit(self::LIMIT)->get()
            : collect();
        $algos = $request->wants('algo')
            ? AlgoOrder::query()->where('env', $env)->whereIn('state', AlgoOrder::OPEN_STATES)
                ->orderByDesc('created_at')->orderByDesc('id')->limit(self::LIMIT)->get()
            : collect();

        return response()->json(['data' => [
            'env' => $env,
            'orders' => OrderResource::collection($orders)->resolve($request),
            'algo_orders' => AlgoOrderResource::collection($algos)->resolve($request),
        ]]);
    }
}
