<?php

declare(strict_types=1);

namespace App\Http\Controllers\Portfolio;

use App\Http\Controllers\Controller;
use App\Http\Requests\Portfolio\BenchmarkRequest;
use App\Support\Portfolio\BenchmarkService;
use Illuminate\Http\JsonResponse;

/**
 * GET /api/portfolio/benchmark (ATL-176, brief §13 Overview benchmark toggle).
 */
class BenchmarkController extends Controller
{
    public function __construct(private readonly BenchmarkService $benchmark) {}

    public function show(BenchmarkRequest $request): JsonResponse
    {
        return response()->json(['data' => $this->benchmark->series($request->env(), $request->symbol(), $request->range())]);
    }
}
