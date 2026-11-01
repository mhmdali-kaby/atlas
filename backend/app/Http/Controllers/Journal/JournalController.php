<?php

declare(strict_types=1);

namespace App\Http\Controllers\Journal;

use App\Casts\PostgresTextArray;
use App\Http\Controllers\Controller;
use App\Http\Requests\Journal\ListJournalRequest;
use App\Http\Requests\Journal\ListWeeklyReviewsRequest;
use App\Http\Resources\JournalEntryResource;
use App\Http\Resources\WeeklyReviewResource;
use App\Support\System\ActiveEnv;
use Illuminate\Database\Query\Builder;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

/**
 * Brief §16 `/api/journal` (ATL-200): the agents' journal (post-mortems and lessons) of the active
 * env, newest first. Read-only; the Portfolio Manager writes it through memory.write_journal.
 */
class JournalController extends Controller
{
    private const COLUMNS = [
        'id', 'env', 'agent', 'inst_id', 'proposal_id', 'title', 'expected_md', 'actual_md',
        'lessons_md', 'tags', 'data', 'created_at',
    ];

    private const WEEKLY_REVIEW_KIND = 'weekly_review';

    public function index(ListJournalRequest $request): JsonResponse
    {
        $env = ActiveEnv::get();
        $tags = $request->tags();
        $outcome = $request->outcome();

        $page = DB::table('journal_entries')
            ->select(self::COLUMNS)
            ->where('env', $env)
            ->when($request->instId(), fn (Builder $q, string $v) => $q->where('inst_id', $v))
            // The agents named or voting in the entry (ATL-194 writes data.agents); the column
            // `agent` is always the Portfolio Manager, the writer.
            ->when($request->agent(), fn (Builder $q, string $v) => $q->whereRaw("data->'agents' @> ?::jsonb", [json_encode([$v], JSON_THROW_ON_ERROR)]))
            // `&&` (overlap) is served by the journal_entries_tags_gin index.
            ->when($tags !== [], fn (Builder $q) => $q->whereRaw('tags && ?::text[]', [PostgresTextArray::serialize($tags)]))
            ->when($outcome, fn (Builder $q, string $v) => $q->where(
                fn (Builder $w) => $w->whereRaw("data->>'outcome' = ?", [$v])
                    ->orWhereRaw('tags @> ?::text[]', [PostgresTextArray::serialize([$v])]),
            ))
            ->when($request->fromUtc(), fn (Builder $q, $v) => $q->where('created_at', '>=', $v->format('Y-m-d H:i:s.uP')))
            ->when($request->toUtc(), fn (Builder $q, $v) => $q->where('created_at', '<', $v->format('Y-m-d H:i:s.uP')))
            ->orderByDesc('created_at')
            ->orderByDesc('id')
            ->cursorPaginate($request->limit())
            ->withQueryString();

        return response()->json([
            'data' => JournalEntryResource::collection($page->items())->resolve($request),
            'meta' => [
                'env' => $env,
                'per_page' => $page->perPage(),
                'next_cursor' => $page->nextCursor()?->encode(),
                'prev_cursor' => $page->previousCursor()?->encode(),
            ],
        ]);
    }

    /**
     * The Historian's Sunday weekly reviews (research_reports of kind weekly_review), newest first.
     */
    public function weeklyReviews(ListWeeklyReviewsRequest $request): JsonResponse
    {
        $env = ActiveEnv::get();
        $page = DB::table('research_reports')
            ->select(['id', 'env', 'agent', 'content_md', 'data', 'created_at'])
            ->where('env', $env)
            ->where('kind', self::WEEKLY_REVIEW_KIND)
            ->orderByDesc('created_at')
            ->orderByDesc('id')
            ->cursorPaginate($request->limit())
            ->withQueryString();

        return response()->json([
            'data' => WeeklyReviewResource::collection($page->items())->resolve($request),
            'meta' => [
                'env' => $env,
                'per_page' => $page->perPage(),
                'next_cursor' => $page->nextCursor()?->encode(),
                'prev_cursor' => $page->previousCursor()?->encode(),
            ],
        ]);
    }

    public function show(Request $request, string $entry): JsonResponse
    {
        $row = DB::table('journal_entries')
            ->select(self::COLUMNS)
            ->where('env', ActiveEnv::get())
            ->where('id', $entry)
            ->first();
        abort_if($row === null, 404);

        return response()->json(['data' => (new JournalEntryResource($row))->resolve($request)]);
    }
}
