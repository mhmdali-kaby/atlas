<?php

declare(strict_types=1);

namespace App\Http\Controllers\Settings;

use App\Http\Controllers\Controller;
use App\Http\Requests\Settings\SettingsEnvRequest;
use App\Http\Requests\Settings\UpdateSettingsRequest;
use App\Http\Resources\SettingsHistoryResource;
use App\Http\Resources\SettingsResource;
use App\Models\SettingsHistory;
use App\Models\User;
use App\Support\Settings\SettingsService;
use App\Support\Settings\StaleSettingsVersion;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Resources\Json\AnonymousResourceCollection;

/**
 * Versioned settings, one row per env (brief §14, §16).
 */
class SettingsController extends Controller
{
    public function __construct(private readonly SettingsService $settings) {}

    public function show(SettingsEnvRequest $request): JsonResponse
    {
        $setting = $this->settings->current($request->env(), $this->owner($request));

        // Always 200: creating the row on first read is an implementation detail, not a POST.
        return (new SettingsResource($setting))->response()->setStatusCode(200);
    }

    public function update(UpdateSettingsRequest $request): JsonResponse
    {
        try {
            $setting = $this->settings->update(
                $request->env(),
                $request->version(),
                $request->changes(),
                $this->owner($request),
            );
        } catch (StaleSettingsVersion $e) {
            return response()->json([
                'message' => __('settings.stale_version'),
                'current_version' => $e->currentVersion,
            ], 409);
        }

        return (new SettingsResource($setting))->response()->setStatusCode(200);
    }

    public function history(SettingsEnvRequest $request): AnonymousResourceCollection
    {
        $setting = $this->settings->current($request->env(), $this->owner($request));
        $perPage = (int) ($request->validated('per_page') ?? 20);

        $page = SettingsHistory::query()
            ->where('settings_id', $setting->id)
            ->with('changedBy')
            ->orderByDesc('version')
            ->paginate($perPage)
            ->withQueryString();

        return SettingsHistoryResource::collection($page);
    }

    private function owner(SettingsEnvRequest|UpdateSettingsRequest $request): ?User
    {
        $user = $request->user();

        return $user instanceof User ? $user : null;
    }
}
