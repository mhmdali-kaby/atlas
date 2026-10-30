<?php

declare(strict_types=1);

namespace App\Http\Requests\Watchlists;

use App\Http\Requests\Settings\SettingsEnvRequest;
use Carbon\CarbonImmutable;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * GET /api/watchlists?date=YYYY-MM-DD&env=demo|live — `date` is an Asia/Baghdad business day,
 * default today there.
 */
class ShowWatchlistRequest extends FormRequest
{
    public function authorize(): bool
    {
        return $this->user() !== null;
    }

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'env' => ['sometimes', 'string', Rule::in(SettingsEnvRequest::envs())],
            'date' => ['sometimes', 'date_format:Y-m-d'],
        ];
    }

    /**
     * @return 'demo'|'live'
     */
    public function env(): string
    {
        return $this->input('env', config('atlas_defaults.env.default')) === 'live' ? 'live' : 'demo';
    }

    public function day(): string
    {
        $date = $this->validated('date');
        if (is_string($date)) {
            return $date;
        }
        $tz = config('atlas_defaults.timezone.display');

        return CarbonImmutable::now(is_string($tz) ? $tz : 'Asia/Baghdad')->toDateString();
    }
}
