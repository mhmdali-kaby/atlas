<?php

declare(strict_types=1);

namespace App\Http\Controllers\Portfolio;

use App\Http\Controllers\Controller;
use App\Http\Requests\Portfolio\PortfolioRequest;
use App\Http\Resources\CashflowResource;
use App\Support\Portfolio\PortfolioService;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Resources\Json\AnonymousResourceCollection;
use Illuminate\Support\Facades\DB;

/**
 * Portfolio API (brief §13, §16). Reads Postgres only; trading_core keeps it in sync with OKX.
 */
class PortfolioController extends Controller
{
    public function __construct(private readonly PortfolioService $portfolio) {}

    public function summary(PortfolioRequest $request): JsonResponse
    {
        return response()->json(['data' => $this->portfolio->summary($request->env())]);
    }

    public function equity(PortfolioRequest $request): JsonResponse
    {
        return response()->json(['data' => $this->portfolio->equity($request->env(), $request->range())]);
    }

    public function holdings(PortfolioRequest $request): JsonResponse
    {
        return response()->json(['data' => $this->portfolio->holdings($request->env())]);
    }

    public function positions(PortfolioRequest $request): JsonResponse
    {
        return response()->json(['data' => $this->portfolio->positions($request->env())]);
    }

    public function cashflows(PortfolioRequest $request): AnonymousResourceCollection
    {
        $query = DB::table('deposits_withdrawals')
            ->where('env', $request->env())
            ->orderByDesc('ts')
            ->orderByDesc('id');
        if ($request->kind() !== null) {
            $query->where('kind', $request->kind());
        }

        return CashflowResource::collection($query->paginate($request->perPage())->withQueryString());
    }
}
