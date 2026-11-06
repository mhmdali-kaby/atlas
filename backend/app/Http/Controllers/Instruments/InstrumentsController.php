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
 * GET /api/instruments?q=&asset_class=&limit=&state= (brief §16, ATL-119, ATL-250). Read-only:
 * reads the `instruments` table of one env (the active env unless ?env= says otherwise), never
 * OKX. q matches anywhere in inst_id or base_ccy, case-insensitively; an exact base match ranks
 * first, then a prefix match, then any other match. Backs the watchlist card's pin search
 * (ATL-115) and the ⌘K palette's Instruments group (ATL-250), so no audit_log entry (brief §16
 * audits writes, not reads). Only `state=live` rows unless ?state=any (the palette shows the
 * OKX state of every instrument).
 */
class InstrumentsController extends Controller
{
    private const COLUMNS = ['inst_id', 'inst_type', 'asset_class', 'base_ccy', 'quote_ccy', 'settle_ccy', 'state'];

    public function index(SearchInstrumentsRequest $request, Blocklist $blocklist): AnonymousResourceCollection
    {
        $env = $request->env();
        $q = $request->search();
        $assetClass = $request->assetClass();
        $lower = mb_strtolower($q);
        $prefix = self::escapeLike($lower).'%';
        $pattern = '%'.self::escapeLike($lower).'%';

        $query = DB::table('instruments')
            ->where('env', $env)
            ->where(function ($inner) use ($pattern): void {
                $inner->whereRaw('lower(inst_id) like ?', [$pattern])
                    ->orWhereRaw('lower(base_ccy) like ?', [$pattern]);
            });

        if (! $request->anyState()) {
            $query->where('state', 'live');
        }

        if ($assetClass !== null) {
            $query->where('asset_class', $assetClass);
        }

        $rows = $query
            // Exact base_ccy match (e.g. "sol" == SOL) ranks first, then a prefix match of
            // inst_id or base_ccy, then a match anywhere ("usdt"), then inst_id alphabetically.
            ->orderByRaw(
                'case when lower(base_ccy) = ? then 0 when lower(inst_id) like ? or lower(base_ccy) like ? then 1 else 2 end',
                [$lower, $prefix, $prefix],
            )
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
