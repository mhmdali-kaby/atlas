<?php

declare(strict_types=1);

namespace App\Http\Controllers\Preferences;

use App\Http\Controllers\Controller;
use App\Http\Requests\Preferences\UpdatePreferencesRequest;
use App\Models\User;
use App\Support\Auditable;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

/**
 * /api/me/preferences (ATL-226, decision #255 (1)): the signed-in owner's name, locale, display
 * timezone and theme. Timezone is display-only; nothing server-side schedules by it.
 */
class PreferencesController extends Controller
{
    public function __construct(private readonly Auditable $audit) {}

    public function show(Request $request): JsonResponse
    {
        return response()->json(['data' => $this->payload($this->owner($request))]);
    }

    public function update(UpdatePreferencesRequest $request): JsonResponse
    {
        $changes = $request->changes();

        $user = DB::transaction(function () use ($request, $changes): User {
            /** @var User $user */
            $user = User::query()->lockForUpdate()->findOrFail($this->owner($request)->id);
            $before = [];
            $after = [];
            foreach ($changes as $field => $value) {
                $old = (string) $user->getAttribute($field);
                if ($old !== $value) {
                    $before[$field] = $old;
                    $after[$field] = $value;
                }
            }
            if ($after !== []) {
                $user->forceFill($after)->save();
                $this->audit->record('preferences.updated', 'users', $user->id, $before, $after, $user);
            }

            return $user;
        });

        return response()->json(['data' => $this->payload($user)]);
    }

    private function owner(Request $request): User
    {
        $user = $request->user();
        abort_unless($user instanceof User, 401);

        return $user;
    }

    /**
     * @return array{name: string, locale: string, timezone: string, theme: string}
     */
    private function payload(User $user): array
    {
        return [
            'name' => $user->name,
            'locale' => $user->locale,
            'timezone' => $user->timezone,
            'theme' => $user->theme,
        ];
    }
}
