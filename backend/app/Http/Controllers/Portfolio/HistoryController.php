<?php

declare(strict_types=1);

namespace App\Http\Controllers\Portfolio;

use App\Http\Controllers\Controller;
use App\Http\Requests\Portfolio\HistoryRequest;
use App\Support\Auditable;
use App\Support\Portfolio\HistoryQuery;
use Illuminate\Http\JsonResponse;
use Symfony\Component\HttpFoundation\StreamedResponse;

/**
 * Portfolio History (brief §13, §16; ATL-172): fills and closed positions, and their CSV export.
 */
class HistoryController extends Controller
{
    /** CSV cells starting with these are prefixed with ' so a spreadsheet never runs them. */
    private const FORMULA_PREFIXES = ['=', '+', '-', '@', "\t", "\r"];

    /** Columns that hold decimals; a leading "-" there is a sign, not a formula. */
    private const NUMERIC_COLUMNS = [
        'price', 'size', 'fee', 'fill_pnl', 'leverage', 'entry', 'exit', 'realised_pnl', 'pnl', 'fees',
        'funding', 'liq_penalty', 'r',
    ];

    public function __construct(private readonly HistoryQuery $history) {}

    public function index(HistoryRequest $request): JsonResponse
    {
        $filters = $request->filters();
        $page = $this->history->build($filters)->cursorPaginate($request->limit())->withQueryString();

        $items = [];
        foreach ($page->items() as $row) {
            $items[] = $this->history->row($filters->type, (object) $row);
        }

        return response()->json([
            'data' => $items,
            'meta' => [
                'type' => $filters->type,
                'per_page' => $page->perPage(),
                'next_cursor' => $page->nextCursor()?->encode(),
                'prev_cursor' => $page->previousCursor()?->encode(),
            ],
        ]);
    }

    public function export(HistoryRequest $request, Auditable $audit, string $type): StreamedResponse
    {
        $filters = $request->filters();
        $audit->record('portfolio.history_exported', 'exports', null, null, $filters->toArray());

        $columns = HistoryQuery::columns($filters->type);
        $query = $this->history->build($filters);
        $history = $this->history;
        $filename = sprintf('atlas-%s-%s-%s.csv', $filters->env, $filters->type, now()->utc()->format('Ymd\THis\Z'));

        return response()->streamDownload(static function () use ($query, $columns, $history, $filters): void {
            $out = fopen('php://output', 'wb');
            if ($out === false) {
                return;
            }
            fputcsv($out, $columns, escape: '');
            foreach ($query->lazy(500) as $row) {
                $values = $history->row($filters->type, (object) $row);
                $line = [];
                foreach ($columns as $column) {
                    $line[] = self::cell($column, $values[$column] ?? null);
                }
                fputcsv($out, $line, escape: '');
            }
            fclose($out);
        }, $filename, ['Content-Type' => 'text/csv; charset=UTF-8']);
    }

    private static function cell(string $column, ?string $value): string
    {
        if ($value === null) {
            return '';
        }
        if (! in_array($column, self::NUMERIC_COLUMNS, true) && $value !== '' && in_array($value[0], self::FORMULA_PREFIXES, true)) {
            return "'".$value;
        }

        return $value;
    }
}
