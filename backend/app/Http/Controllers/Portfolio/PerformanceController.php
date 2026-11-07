<?php

declare(strict_types=1);

namespace App\Http\Controllers\Portfolio;

use App\Http\Controllers\Controller;
use App\Http\Requests\Portfolio\PerformanceRequest;
use App\Support\Portfolio\PerformanceService;
use Illuminate\Http\JsonResponse;

/**
 * Portfolio Performance (brief §13, §16; ATL-174). Reads Postgres only.
 */
class PerformanceController extends Controller
{
    public function __construct(private readonly PerformanceService $performance) {}

    public function show(PerformanceRequest $request): JsonResponse
    {
        return response()->json(['data' => $this->performance->performance($request->env(), $request->range())]);
    }
}
