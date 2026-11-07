<?php

declare(strict_types=1);

namespace App\Http\Requests\Proposals;

use App\Models\Proposal;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Support\Carbon;
use Illuminate\Validation\Rule;

/**
 * GET /api/proposals?env=demo&status=awaiting_owner&inst=BTC-USDT-SWAP&date=2026-10-31&limit=50
 * `status` may be one value or a comma list; `date` is a Baghdad business day.
 */
class ListProposalsRequest extends FormRequest
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
            'env' => ['sometimes', Rule::in(['demo', 'live'])],
            'status' => ['sometimes', 'string', 'max:300', function (string $attribute, mixed $value, \Closure $fail): void {
                foreach (explode(',', (string) $value) as $s) {
                    if (! in_array(trim($s), Proposal::STATUSES, true)) {
                        $fail(__('proposals.status_unknown', ['status' => trim($s)]));

                        return;
                    }
                }
            }],
            'inst' => ['sometimes', 'string', 'max:64', 'regex:/^[A-Za-z0-9][A-Za-z0-9._-]*$/'],
            'date' => ['sometimes', 'date_format:Y-m-d'],
            'limit' => ['sometimes', 'integer', 'min:1', 'max:'.self::intDefault('list_max_limit', 200)],
            'cursor' => ['sometimes', 'string', 'max:500'],
        ];
    }

    public function env(): string
    {
        return $this->input('env', config('atlas_defaults.env.default')) === 'live' ? 'live' : 'demo';
    }

    /** @return list<string> */
    public function statuses(): array
    {
        $raw = $this->validated('status');

        return is_string($raw) ? array_values(array_unique(array_map('trim', explode(',', $raw)))) : [];
    }

    public function instId(): ?string
    {
        $inst = $this->validated('inst');

        return is_string($inst) ? strtoupper($inst) : null;
    }

    /** @return array{0: Carbon, 1: Carbon}|null the UTC bounds of the Baghdad day */
    public function dayBounds(): ?array
    {
        $date = $this->validated('date');
        if (! is_string($date)) {
            return null;
        }
        $tz = config('atlas_defaults.timezone.display');
        $start = Carbon::createFromFormat('Y-m-d', $date, is_string($tz) ? $tz : 'Asia/Baghdad');
        if ($start === null) {
            return null;
        }
        $start = $start->startOfDay();

        return [$start->copy()->utc(), $start->copy()->addDay()->utc()];
    }

    public function limit(): int
    {
        $limit = $this->validated('limit');

        return is_numeric($limit) ? (int) $limit : self::intDefault('list_limit', 50);
    }

    private static function intDefault(string $key, int $fallback): int
    {
        $value = config("atlas_defaults.proposals_api.{$key}");

        return is_int($value) ? $value : $fallback;
    }
}
