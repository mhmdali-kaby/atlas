<?php

declare(strict_types=1);

namespace App\Http\Requests\Warroom;

use App\Http\Requests\Settings\SettingsEnvRequest;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * Base for every /api/warroom/* request: ?env=demo|live (or "env" in a POST body), defaulting to
 * the default env, like the Settings and Portfolio APIs.
 */
abstract class WarroomRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'env' => ['sometimes', 'string', Rule::in(SettingsEnvRequest::envs())],
        ];
    }

    /**
     * @return 'demo'|'live'
     */
    public function env(): string
    {
        $env = $this->input('env', config('atlas_defaults.env.default'));

        return $env === 'live' ? 'live' : 'demo';
    }

    protected static function intDefault(string $key, int $fallback): int
    {
        $value = config("atlas_defaults.warroom.{$key}");

        return is_int($value) ? $value : $fallback;
    }
}
