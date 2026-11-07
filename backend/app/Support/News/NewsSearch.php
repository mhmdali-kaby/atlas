<?php

declare(strict_types=1);

namespace App\Support\News;

/**
 * Shared SQL fragments for the read-only news API (ATL-67). The full-text document expression
 * must stay byte-identical to the expression index news_items_fts_gin, or Postgres will not use
 * the index for `q`.
 */
final class NewsSearch
{
    /** Brief §7.2 step 4 (same list and case as the news_items.event_type enum). */
    public const EVENT_TYPES = [
        'earnings', 'guidance', 'macro_release', 'central_bank', 'regulation', 'hack_exploit',
        'listing', 'delisting', 'ETF_flow', 'M&A', 'lawsuit', 'product', 'partnership',
        'token_unlock', 'geopolitics', 'supply_shock', 'other',
    ];

    /** Asset classes as derived for instruments (brief §4.3). */
    public const ASSET_CLASSES = ['crypto', 'stock', 'etf', 'index', 'commodity', 'other'];

    /** Text-search document over title + summary; indexed by news_items_fts_gin. */
    public const TS_DOCUMENT = "to_tsvector('english'::regconfig, coalesce(title, '') || ' ' || coalesce(summary, ''))";

    /** websearch syntax: quoted phrases, OR and -term, and never a syntax error on user input. */
    public const TS_QUERY = "websearch_to_tsquery('english'::regconfig, ?)";

    /** Upper bound on q, to keep tsquery construction cheap. */
    public const MAX_QUERY_LENGTH = 200;
}
