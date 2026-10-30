<?php

declare(strict_types=1);

namespace App\Http\Controllers\Instruments;

use App\Http\Controllers\Controller;
use App\Http\Requests\Instruments\SearchInstrumentsRequest;
use App\Http\Resources\InstrumentResource;
use App\Support\Watchlists\Blocklist;
use Illuminate\Http\Resources\Json\AnonymousResourceCollection;
use Illuminate\Support\Facades\DB;

/**
 * GET /api/instruments?q=&asset_class=&limit= (brief §16, ATL-119). Read-only: reads the
 * `instruments` table of the active env only, never OKX. Backs the watchlist card's pin search
 * (ATL-115), so no audit_log entry (brief §16 audits writes, not reads).
 */
class InstrumentsController extends Controller
{
    private const COLUMNS = ['inst_id', 'inst_type', 'asset_class', 'base_ccy', 'quote_ccy', 'settle_ccy'];

    public function index(SearchInstrumentsRequest $request, Blocklist $blocklist): AnonymousResourceCollection
    {
        $env = $request->env();
        $q = $request->search();
        $assetClass = $request->assetClass();
        $pattern = mb_strtolower(self::escapeLike($q)).'%';

        $query = DB::table('instruments')
            ->where('env', $env)
            ->where('state', 'live')
            ->where(function ($inner) use ($pattern): void {
                $inner->whereRaw('lower(inst_id) like ?', [$pattern])
                    ->orWhereRaw('lower(base_ccy) like ?', [$pattern]);
            });

        if ($assetClass !== null) {
            $query->where('asset_class', $assetClass);
        }

        $rows = $query
            // Exact base_ccy match (e.g. "sol" == SOL) ranks before a mere prefix match, then
            // inst_id alphabetically.
            ->orderByRaw('case when lower(base_ccy) = ? then 0 else 1 end', [mb_strtolower($q)])
            ->orderBy('inst_id')
            ->limit($request->limit())
            ->get(self::COLUMNS);

        $blocked = $blocklist->for($env);
        foreach ($rows as $row) {
            $row->blocked = in_array(mb_strtoupper((string) $row->inst_id), $blocked, true);
        }

        return InstrumentResource::collection($rows);
    }

    /** Postgres' default LIKE escape character is backslash; q may contain % or _ literally. */
    private static function escapeLike(string $value): string
    {
        return str_replace(['\\', '%', '_'], ['\\\\', '\\%', '\\_'], $value);
    }
}
