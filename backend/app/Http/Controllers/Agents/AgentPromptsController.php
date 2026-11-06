<?php

declare(strict_types=1);

namespace App\Http\Controllers\Agents;

use App\Http\Controllers\Controller;
use App\Http\Requests\Agents\PinPromptRequest;
use App\Http\Requests\Settings\SettingsEnvRequest;
use App\Models\User;
use App\Support\Agents\PromptVersions;
use App\Support\Settings\SettingsSchema;
use App\Support\Settings\SettingsService;
use App\Support\Settings\StaleSettingsVersion;
use App\Support\System\ActiveEnv;
use Illuminate\Http\JsonResponse;
use Illuminate\Validation\ValidationException;

/**
 * ATL-251, brief §14.7, decision #265 (3): an agent's stored prompt versions, one version's raw
 * body, and the pin `agents.<slug>.prompt_version`, written through SettingsService so it is
 * versioned, audited and broadcast like every other setting.
 */
class AgentPromptsController extends Controller
{
    public function index(SettingsEnvRequest $request, SettingsService $settings, string $slug): JsonResponse
    {
        self::assertAgent($slug);
        $env = $request->has('env') ? $request->env() : ActiveEnv::get();
        $pinned = self::pinned($settings, $env, $slug);

        return response()->json([
            'data' => PromptVersions::list($slug, $env, $pinned),
            'meta' => ['agent' => $slug, 'env' => $env, 'pinned_version' => $pinned],
        ]);
    }

    public function show(string $slug, int $version): JsonResponse
    {
        self::assertAgent($slug);
        $found = PromptVersions::find($slug, $version);
        abort_if($found === null, 404);

        return response()->json(['data' => $found]);
    }

    public function pin(PinPromptRequest $request, SettingsService $settings, string $slug): JsonResponse
    {
        self::assertAgent($slug);
        $version = $request->version();
        if ($version !== null && ! PromptVersions::exists($slug, $version)) {
            throw ValidationException::withMessages([
                'version' => __('settings.prompt_version_unknown', ['version' => $version]),
            ]);
        }

        $env = $request->env();
        $field = SettingsSchema::agentField($slug, 'prompt_version');
        $user = $request->user();
        $user = $user instanceof User ? $user : null;

        // A concurrent settings save between read and write retries once on the new version.
        $saved = null;
        for ($attempt = 0; $attempt < 2 && $saved === null; $attempt++) {
            $current = $settings->current($env, $user);
            try {
                $saved = $settings->update($env, $current->version, [$field => $version], $user);
            } catch (StaleSettingsVersion $e) {
                if ($attempt === 1) {
                    return response()->json([
                        'message' => __('settings.stale_version'),
                        'current_version' => $e->currentVersion,
                    ], 409);
                }
            }
        }

        return response()->json([
            'data' => [
                'agent' => $slug,
                'env' => $env,
                'pinned_version' => $version,
                'settings_version' => $saved?->version,
            ],
        ]);
    }

    private static function assertAgent(string $slug): void
    {
        abort_unless(in_array($slug, SettingsSchema::agentSlugs(), true), 404);
    }

    private static function pinned(SettingsService $settings, string $env, string $slug): ?int
    {
        $value = $settings->values($settings->current($env))[SettingsSchema::agentField($slug, 'prompt_version')] ?? null;

        return is_int($value) ? $value : null;
    }
}
