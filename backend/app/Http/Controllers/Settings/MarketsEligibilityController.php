<?php

declare(strict_types=1);

namespace App\Http\Controllers\Settings;

use App\Http\Controllers\Controller;
use App\Http\Requests\Settings\MarketsEligibilityRequest;
use App\Support\Settings\MarketsEligibility;
use Illuminate\Http\JsonResponse;

/**
 * GET /api/settings/markets/eligibility?env=demo&inst_ids[]=XAU-USDT-SWAP[&draft[...]=...]
 *
 * Read-only: never writes a settings version, even when the env has no settings row yet.
 */
final class MarketsEligibilityController extends Controller
{
    public function __construct(private readonly MarketsEligibility $eligibility) {}

    public function __invoke(MarketsEligibilityRequest $request): JsonResponse
    {
        return response()->json([
            'data' => $this->eligibility->check($request->env(), $request->instIds(), $request->draft()),
        ]);
    }
}
