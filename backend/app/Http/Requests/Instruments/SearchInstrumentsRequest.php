<?php

declare(strict_types=1);

namespace App\Http\Requests\Instruments;

use App\Http\Requests\Settings\SettingsEnvRequest;
use App\Support\News\NewsSearch;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * GET /api/instruments?q=&asset_class=&limit=&env= (brief §16, ATL-119). Backs the watchlist
 * card's pin search (ATL-115): q is matched case-insensitively against the inst_id or base_ccy
 * prefix (e.g. "sol" -> SOL-USDT-SWAP, SOL-USDT). `env` defaults to the default env, the same
 * convention as the Data, Portfolio and War Room APIs.
 */
class SearchInstrumentsRequest extends FormRequest
{
    public const LIMIT_DEFAULT = 20;

    public const LIMIT_MAX = 50;

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
            'q' => ['required', 'string', 'min:1', 'max:32'],
            'asset_class' => ['sometimes', 'string', Rule::in(NewsSearch::ASSET_CLASSES)],
            'limit' => ['sometimes', 'integer', 'min:1', 'max:'.self::LIMIT_MAX],
            'env' => ['sometimes', 'string', Rule::in(SettingsEnvRequest::envs())],
        ];
    }

    public function search(): string
    {
        return trim((string) $this->validated('q'));
    }

    public function assetClass(): ?string
    {
        $value = $this->validated('asset_class');

        return is_string($value) ? $value : null;
    }

    public function limit(): int
    {
        $limit = $this->validated('limit');

        return is_numeric($limit) ? (int) $limit : self::LIMIT_DEFAULT;
    }

    /**
     * @return 'demo'|'live'
     */
    public function env(): string
    {
        $env = $this->validated('env') ?? config('atlas_defaults.env.default');

        return $env === 'live' ? 'live' : 'demo';
    }
}
