<?php

declare(strict_types=1);

namespace App\Http\Requests\Instruments;

use App\Http\Requests\Settings\SettingsEnvRequest;
use App\Support\News\NewsSearch;
use App\Support\System\ActiveEnv;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

/**
 * GET /api/instruments?q=&asset_class=&limit=&state=&env= (brief §16, ATL-119, ATL-250). q is
 * matched case-insensitively anywhere in inst_id or base_ccy (e.g. "sol" -> SOL-USDT-SWAP,
 * SOL-USDT; "xau" -> XAU-USDT-SWAP, XAUT-USDT-SWAP). `state=any` includes instruments OKX lists
 * as suspended, pre-open or test (default: live only). `env` defaults to the active env
 * (decision #255 (4)).
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
            'state' => ['sometimes', 'string', Rule::in(['live', 'any'])],
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

    public function anyState(): bool
    {
        return $this->validated('state') === 'any';
    }

    /**
     * @return 'demo'|'live'
     */
    public function env(): string
    {
        $env = $this->validated('env') ?? ActiveEnv::get();

        return $env === 'live' ? 'live' : 'demo';
    }
}
