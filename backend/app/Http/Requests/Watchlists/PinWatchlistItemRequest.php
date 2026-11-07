<?php

declare(strict_types=1);

namespace App\Http\Requests\Watchlists;

use App\Support\Watchlists\Blocklist;
use Closure;
use Illuminate\Support\Facades\DB;

/**
 * POST /api/watchlists/{id}/pin { "inst_id": "SOL-USDT-SWAP" } — the instrument must exist in
 * the watchlist's env, have state "live", and not be on that env's settings blocklist.
 */
class PinWatchlistItemRequest extends EditWatchlistRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        $env = $this->watchlist()->env;

        return [
            'inst_id' => [
                'required', 'string', 'max:64', 'regex:/^[A-Za-z0-9][A-Za-z0-9._-]*$/',
                function (string $attribute, mixed $value, Closure $fail) use ($env): void {
                    if (! is_string($value)) {
                        return;
                    }
                    $instrument = DB::table('instruments')
                        ->where('env', $env)
                        ->where('inst_id', strtoupper($value))
                        ->first(['state']);
                    if ($instrument === null) {
                        $fail(__('watchlists.inst_unknown', ['env' => $env]));

                        return;
                    }
                    if ($instrument->state !== 'live') {
                        $fail(__('watchlists.inst_not_live', ['state' => (string) $instrument->state]));

                        return;
                    }
                    if (app(Blocklist::class)->contains($env, $value)) {
                        $fail(__('watchlists.inst_blocked'));
                    }
                },
            ],
        ];
    }

    public function instId(): string
    {
        return strtoupper((string) $this->validated('inst_id'));
    }
}
