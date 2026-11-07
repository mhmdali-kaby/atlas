<?php

declare(strict_types=1);

namespace App\Http\Controllers\Settings;

use App\Http\Controllers\Controller;
use App\Http\Requests\Settings\RiskPreviewRequest;
use App\Models\User;
use App\Support\Settings\RiskPreviewService;
use Illuminate\Http\JsonResponse;

/**
 * GET /api/settings/risk-preview (brief §14.4): the Settings page's live "next trade can risk at
 * most $Y and use at most $Z" preview. Read-only: a draft query param never writes a version.
 */
class RiskPreviewController extends Controller
{
    public function __construct(private readonly RiskPreviewService $preview) {}

    public function show(RiskPreviewRequest $request): JsonResponse
    {
        $user = $request->user();

        return response()->json([
            'data' => $this->preview->preview($request->env(), $request->drafts(), $user instanceof User ? $user : null),
        ]);
    }
}
