<?php

declare(strict_types=1);

namespace Database\Seeders;

use App\Models\WarroomThread;
use Illuminate\Database\Seeder;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;
use RuntimeException;

/**
 * Fixture War Room for the Playwright flows and demos (ATL-39, rewritten by ATL-254): 500 messages
 * in the demo Main thread, one every two minutes, ending with an owner message at
 * 2026-10-28T02:00:00Z (05:00 in Baghdad); plus an empty BTC-USDT-SWAP thread and an empty private
 * Historian thread.
 *
 * The text reads like a real demo trading day on the eve of US CPI: each author type draws from
 * its own pool of hand-written messages, in order, so nothing carries a counter. Prices sit in
 * plausible late-October 2026 ranges (BTC about 111,000–116,000, ETH about 3,900–4,100, gold about
 * 4,000–4,100, TSLA about 430–450, NVDA about 185–195, SPY about 680–690). A few owner messages
 * are in Arabic. The mix still covers every renderer: agent text, owner bubbles, a reply, a system
 * event line, a system alert and a watchlist card. Never runs in production.
 */
class WarroomDemoSeeder extends Seeder
{
    public const COUNT = 500;

    public const LAST_AT = '2026-10-28T02:00:00Z';

    public const LAST_OWNER_TEXT = 'Hold new entries until CPI at 15:30, then send me the plan.';

    /** The owner question (index 482) that the second-to-last message replies to. */
    public const CPI_QUESTION_TEXT = '@Historian what usually happens after CPI to gold, and how fast?';

    /** The Historian's answer, second-to-last in the thread, replying to CPI_QUESTION_TEXT. */
    public const CPI_REPLY_TEXT = 'Over the last 41 CPI releases, XAU-USDT-SWAP moved a median 1.4 ATR(1H) in the first hour (p10 0.6, p90 2.7); direction split 22 up / 19 down, so the size of the move is the edge, not the side. n = 41: moderate evidence.';

    /** A Historian message well up the feed (index 480), the scrolled-up reply target in warroom-reply.spec.ts. */
    public const OLDER_HISTORIAN_TEXT = 'Regime check before CPI: SPY-USDT-SWAP trending (HMM p = 0.71, 9 days in regime), XAU-USDT-SWAP high-vol range (p = 0.64, 4 days). Event study, SPY on CPI days since 2018: median absolute move 1.1% (n = 58).';

    private const OWNER_QUESTION_INDEX = 482;

    private const OLDER_HISTORIAN_INDEX = 480;

    /**
     * Agent messages in the order the day unfolds: [author, content_md, mentions].
     *
     * @var list<array{0: string, 1: string, 2: list<string>}>
     */
    private const AGENT_POOL = [
        ['orchestrator', 'Analysis cycle open: watchlist BTC-USDT-SWAP, XAU-USDT-SWAP, TSLA-USDT-SWAP, NVDA-USDT-SWAP, SPY-USDT-SWAP. Research due in 5 min.', []],
        ['technical_analyst', 'BTC-USDT-SWAP, 1H: range 112,400–114,900 for 31 bars, EMA20 flat, ADX 14. No setup. A 1H close above 114,900 with relative volume > 1.3 turns this into a breakout long.', []],
        ['derivatives_sentiment', 'BTC-USDT-SWAP: funding +0.008% (7d z 0.4), OI −2.1% over 4h while price is flat. Positioning is NEUTRAL to a long; nobody is crowded yet.', []],
        ['news_intelligence', 'CONFIRMED (BLS calendar): US CPI for September prints tomorrow 12:30 UTC, 15:30 Baghdad. Consensus 0.3% m/m, previous 0.4%. Most sensitive here: XAU, SPY, QQQ, then BTC.', []],
        ['historian', 'XAU-USDT-SWAP regime: high-vol range, HMM probability 0.64, 4 days in regime. Breakout signals in this regime have held 38% of the time (n = 26). Weak support for breakout entries.', []],
        ['market_scout', 'Backdrop: BTC ranging near 113,500 with realised vol at the 30th percentile; SPY 3 sessions up into CPI; gold off its high but bid on dips. CPI and Thursday’s FOMC dominate the next 48h.', []],
        ['strategist', 'No trade on BTC-USDT-SWAP this cycle: Technical has no setup and the range midpoint gives R 0.9 to either edge, below the 1.8 minimum.', []],
        ['devils_advocate', 'On the TSLA idea: the stock perp is in its overnight session, spread 6.5 bps vs 2.1 in US hours. Slippage at full size eats 0.2R before the trade starts.', []],
        ['portfolio_manager', 'ETH-USDT-SWAP long: +0.6R, stop 3,912, TP1 4,068 untouched, 5h 40m in trade, funding paid 3.10 USDT. Thesis intact; no rule fires.', []],
        ['orchestrator', 'Research collected: Technical, Derivatives, News and Historian all in. Strategist, your call on XAU and TSLA.', ['strategist']],
        ['technical_analyst', 'XAU-USDT-SWAP, 4H: pullback to the 4,012–4,026 zone (strength 0.72, 5 touches). 1H RSI 41 with a bullish divergence. Pullback-to-support long, entry 4,018–4,026, stop 3,994, TP1 4,071 (1.9R).', []],
        ['derivatives_sentiment', 'XAU-USDT-SWAP: perp premium +0.04% vs the index, OI +3.8% in 24h with price −0.9% = new shorts. A short squeeze into CPI is possible, probability 0.35, trigger a 1H close above 4,050.', []],
        ['news_intelligence', 'REPORTED (Reuters): central-bank gold buying stayed strong in Q3, per the World Gold Council’s quarterly report. Already known since Thursday; priced in. Expected impact on XAU small (< 0.5 ATR).', []],
        ['historian', 'XAU-USDT-SWAP, 20 nearest analogs to the current 4H pullback: forward 24h median +0.6%, p10 −1.3%, p90 +2.1%, 60% positive vs 53% base rate. History weakly supports the long.', []],
        ['strategist', 'XAU-USDT-SWAP long proposed: limit 4,022, stop 3,994, TP1 4,071 (50%), TP2 4,098 (30%), TP3 4,130 (20%). p(TP1 before stop) 0.48, R 1.9, expectancy +0.39. Size 2.5% of equity, 3x.', []],
        ['devils_advocate', 'Strongest objection to the XAU long: CPI lands 17h into the 24h time stop, and the 28-point stop sits inside the median 1h CPI move (1.4 ATR). Risk rating 4. Fix: cut the time stop to end before CPI, or halve size.', ['strategist']],
        ['strategist', 'Accepted: time stop now 15:00 Baghdad, 30 min before CPI. Revised to version 2; size unchanged at 2.5%.', ['devils_advocate']],
        ['devils_advocate', 'Re-rated the XAU long at 2 with the time stop before CPI. Vote: take, confidence 0.62.', []],
        ['orchestrator', 'Vote closed on XAU-USDT-SWAP long v2: weighted score 0.71 against the 0.6 threshold, Devil’s Advocate rating 2. Sent to the Risk Engine.', []],
        ['portfolio_manager', 'Portfolio: 2 open positions, open risk 0.9% of equity, today’s PnL +0.21% against a 4% daily loss limit. Correlated exposure 12% of a 35% cap.', []],
        ['technical_analyst', 'TSLA-USDT-SWAP, 1D: post-earnings gap at 438–442 still unfilled; price 446.80. Squeeze flag on the 4H (Bollinger width at the 7th percentile). No trigger yet; a US-hours close below 438 would be a breakdown short.', []],
        ['derivatives_sentiment', 'TSLA-USDT-SWAP: premium to the underlying +0.62% outside US hours. Gaps like this have closed at the next US open most of the time; expected direction down, small.', []],
        ['news_intelligence', 'RUMOUR (social, unverified): a post claims a Tesla robotaxi expansion announcement this week. No reputable outlet has it. Not a reason to trade.', []],
        ['historian', 'TSLA-USDT-SWAP has 74 days of OKX history; I used NASDAQ reference data (labelled reference, not tradeable prices) for the event study. After CPI, TSLA median absolute 1-day move 3.2% (n = 35).', []],
        ['market_scout', 'Intraday re-run: NVDA-USDT-SWAP volatility spiked 3.4σ on an export-licence headline. Adding it to the review list; not yet a watchlist pick.', ['news_intelligence']],
        ['news_intelligence', 'NVDA: REPORTED (Bloomberg) that the US Commerce Department is reviewing new chip export licences. One outlet only. NVDA-USDT-SWAP moved −1.8% (0.9 ATR) since first seen; partly priced in.', []],
        ['technical_analyst', 'NVDA-USDT-SWAP, 1H: sold into the 186.20–187.00 zone (strength 0.66) and bounced. Range 186.20–193.40; no setup until earnings guidance or a 1H close outside the range.', []],
        ['derivatives_sentiment', 'NVDA-USDT-SWAP: taker sell/buy 1.4 over the last hour, long/short account ratio 2.3 and rising. Longs are crowded into the dip; long-squeeze risk 0.4 below 186.20.', []],
        ['strategist', 'NVDA: no trade. Expectancy at the range edge is +0.08 with p 0.42 and R 1.6, below the +0.2 margin. Revisit after the 1H close.', []],
        ['orchestrator', 'Where we are: watchlist 5 of 5 active, 2 open positions, PnL today +0.21%, 61% of the daily trade budget left. Next cycle in 30 min.', []],
        ['portfolio_manager', 'XAU-USDT-SWAP long reached +1R: stop moved to breakeven plus fees at 4,023.40. Rule: breakeven at +1R.', []],
        ['technical_analyst', 'SPY-USDT-SWAP, 4H: trend up, EMA 20/50/200 aligned (+3), but price is 2.1 ATR above the 683.50 level. Extended; no entry until a pullback toward 683–685.', []],
        ['derivatives_sentiment', 'SPY-USDT-SWAP: OI +6% in 24h with price +0.7% = new longs. Funding +0.012%, z 1.6. Supports a long, but crowding is building into CPI.', []],
        ['historian', 'Stop-noise check for the BTC-USDT-SWAP breakout idea: a stop at 113,500 under a 114,900 entry is 0.62x the median 4h range (1.98%). That is inside normal noise. Widen it to 0.8x or skip.', ['strategist']],
        ['devils_advocate', 'Liquidity check: weekend-like volume on BTC-USDT-SWAP tonight, top-50 depth 38% below its 30-day median. Estimated slippage at max size 4.1 bps. OK, but watch it.', []],
        ['news_intelligence', 'Morning check of the calendar: FOMC decision Thursday 18:00 UTC (21:00 Baghdad), consensus hold. US NFP next Friday. No OKX maintenance scheduled for the trading window.', []],
        ['market_scout', 'Rejected for now: SOL-USDT-SWAP (move already 3.4 ATR, chasing), XAG-USDT-SWAP (spread 9 bps, above the 8 bps cap), QQQ-USDT-SWAP (0.93 correlation with SPY, which is already on the list).', []],
        ['portfolio_manager', 'ETH-USDT-SWAP long: funding has flipped against us, annualised 11%. Below the 30% flag level; holding.', []],
        ['strategist', 'BTC-USDT-SWAP: Historian is right, the stop sat inside noise. Withdrawn; I will re-propose only on a confirmed 1H close above 114,900.', ['historian']],
        ['orchestrator', 'Cycle closed: 1 proposal sent (XAU long v2), 3 instruments no-trade, 0 Risk Engine rejections. Next cycle on the half hour.', []],
        ['technical_analyst', 'ETH-USDT-SWAP, 1H: structure intact, higher lows at 3,936 and 3,958. Trailing stop suggestion 3,941 once price clears 4,040 (+1.5R).', ['portfolio_manager']],
        ['derivatives_sentiment', 'Market-wide: total crypto perp OI −1.4% today, Fear & Greed 52 (neutral). Nothing one-sided; quiet positioning ahead of CPI.', []],
        ['news_intelligence', 'CONFIRMED (Fed, two outlets): no Fed speakers scheduled before CPI. The next scheduled speech is after the FOMC decision on Thursday.', []],
        ['historian', 'Seasonality, BTC-USDT-SWAP by hour (UTC, last 2 years): the 13:00–15:00 window has the highest average range, 0.71% per hour vs 0.44% for the day. US cash open matters even for crypto.', []],
        ['devils_advocate', 'On an ETH add: we already hold ETH long and BTC–ETH 30-day correlation is 0.86. A second crypto long pushes correlated exposure to 24%. Not a breach, but skip unless the setup is clearly better.', []],
    ];

    /**
     * Owner messages, drawn in order: [content_md, mentions]. Some are in Arabic.
     *
     * @var list<array{0: string, 1: list<string>}>
     */
    private const OWNER_POOL = [
        ['Morning. Anything worth doing on gold today, or do we wait for CPI?', []],
        ['@Strategist why is TSLA not on the list after earnings?', ['strategist']],
        ['شنو وضع الذهب قبل بيانات التضخم الأمريكية؟ نفتح مركز لو ننتظر؟', []],
        ['@Historian how did BTC behave around the last three FOMC meetings?', ['historian']],
        ['Keep the XAU size at 2.5%, I am fine with that.', []],
        ['@Devil is the NVDA headline a reason to stay away until Thursday?', ['devils_advocate']],
        ['@Devil هل خطر الفجوة على تسلا بعد إغلاق السوق الأمريكي كبير؟', ['devils_advocate']],
        ['Good call withdrawing the BTC breakout. Agree the stop was too tight.', []],
        ['@PM what is our total open risk right now in USD?', ['portfolio_manager']],
        ['أريد ملخصاً قصيراً لما حدث اليوم قبل أن أنام، من فضلكم.', []],
        ['Do not add to ETH tonight. One crypto long is enough before CPI.', []],
        ['@News is the robotaxi story confirmed anywhere yet?', ['news_intelligence']],
        ['شكد صار ربح صفقة الذهب؟ وين الستوب هسه؟', []],
        ['@Technical where would SPY need to pull back to before you would buy it?', ['technical_analyst']],
        ['Please make sure nothing is open on stock perps through the US close tomorrow.', []],
        ['@Scout why did you reject silver? The move looked clean to me.', ['market_scout']],
        ['لا تفتحوا أي صفقة جديدة على الأسهم خلال الليل، السيولة ضعيفة.', []],
        ['@Strategist if CPI comes in hot, what is the plan for gold?', ['strategist']],
        ['Thanks, the overnight summary was useful. Keep it that short.', []],
        ['@Orchestrator brief me on where we are before I sign off.', ['orchestrator']],
        ['@Derivatives is SPY crowding getting worse or stable?', ['derivatives_sentiment']],
        ['هل الفوركس أو النفط فيه فرص هذا الأسبوع؟ حتى لو صغيرة.', []],
    ];

    /** System event lines, drawn in order. @var list<string> */
    private const SYSTEM_POOL = [
        'Synced the OKX demo account: equity, 2 positions and 3 algo orders match.',
        'Instruments refreshed: 188 SWAP instruments in the demo universe, 2 new, 0 delisted.',
        'Settings changed to version 14 by the owner: max open positions 6.',
        'Reconciled with OKX: no differences.',
        'Market data feed reconnected after 3 s; resubscribed to 14 channels.',
        'Clock offset to OKX is 38 ms.',
        'Order filled: XAU-USDT-SWAP buy 25 contracts at 4,022.00. Stop 3,994.00 confirmed on OKX.',
        'Backfill complete for NVDA-USDT-SWAP 1H: 1,781 bars.',
        'Reconciled with OKX: no differences.',
        'Synced the OKX demo account: equity and positions match; bills up to date.',
    ];

    /** System alerts, drawn in order: [severity, title, content_md]. @var list<array{0: string, 1: string, 2: string}> */
    private const ALERT_POOL = [
        ['warning', 'Market data is late', 'No ticker for 42 s on XAU-USDT-SWAP. New orders wait until it is fresh.'],
        ['warning', 'Source degraded', 'CoinDesk RSS failed 3 times in a row and is paused for 30 min. Other crypto sources are working.'],
        ['info', 'Untrusted text flagged', 'A news item about TSLA-USDT-SWAP contained instruction-like text ("ignore previous rules"). It was quoted as data and not followed.'],
        ['warning', 'Spread widened', 'TSLA-USDT-SWAP spread is 11 bps in the overnight session, above the 8 bps cap. It is excluded until the spread narrows.'],
        ['warning', 'Agent rate-limited', 'News Intelligence hit the Claude usage limit and is paused for 20 min. The cycle continues without it.'],
    ];

    /** Market Scout watchlist cards, drawn in order. @var list<string> */
    private const WATCHLIST_POOL = [
        "1. **XAU-USDT-SWAP**, long, 0.58\n2. **TSLA-USDT-SWAP**, short, 0.46\n3. **SPY-USDT-SWAP**, long, 0.52",
        "1. **BTC-USDT-SWAP**, neutral, 0.40\n2. **XAU-USDT-SWAP**, long, 0.58\n3. **NVDA-USDT-SWAP**, short, 0.44",
        "1. **XAU-USDT-SWAP**, long, 0.61 (partial evidence)\n2. **SPY-USDT-SWAP**, long, 0.50",
        "1. **ETH-USDT-SWAP**, long, 0.55\n2. **XAU-USDT-SWAP**, long, 0.57\n3. **TSLA-USDT-SWAP**, short, 0.45",
        "1. **XAU-USDT-SWAP**, long, 0.56\n2. **NVDA-USDT-SWAP**, neutral, 0.38",
    ];

    public function run(): void
    {
        if (app()->environment('production')) {
            throw new RuntimeException('WarroomDemoSeeder refuses to run when APP_ENV=production.');
        }

        $this->call(WarroomThreadSeeder::class);
        /** @var WarroomThread $main */
        $main = WarroomThread::query()->where(['env' => 'demo', 'kind' => 'main'])->firstOrFail();
        WarroomThread::query()->firstOrCreate(['env' => 'demo', 'kind' => 'instrument', 'key' => 'BTC-USDT-SWAP'], ['title' => 'BTC-USDT-SWAP']);
        WarroomThread::query()->firstOrCreate(['env' => 'demo', 'kind' => 'agent', 'key' => 'historian'], ['title' => 'historian']);

        $last = Carbon::parse(self::LAST_AT);
        $draws = ['agent' => 0, 'owner' => 0, 'system' => 0, 'alert' => 0, 'watchlist' => 0];
        $rows = [];
        $ids = [];
        for ($i = 0; $i < self::COUNT; $i++) {
            $at = $last->copy()->subMinutes(2 * (self::COUNT - 1 - $i))->format('Y-m-d H:i:s.uP');
            $id = (string) Str::uuid();
            $ids[] = $id;
            $row = [
                'id' => $id, 'env' => 'demo', 'thread_id' => $main->id, 'event_id' => null,
                'author_type' => 'agent', 'author' => 'orchestrator', 'kind' => 'text',
                'content_md' => '', 'card' => null, 'reply_to' => null, 'mentions' => '{}', 'reasoning_md' => null, 'tool_calls' => null,
                'tokens_in' => null, 'tokens_out' => null, 'cost_usd' => null, 'created_at' => $at, 'updated_at' => $at,
            ];

            if ($i === self::COUNT - 1) {
                $row = array_merge($row, ['author_type' => 'human', 'author' => E2eSeeder::NAME, 'content_md' => self::LAST_OWNER_TEXT]);
            } elseif ($i === self::COUNT - 2) {
                $row = array_merge($row, ['author' => 'historian', 'content_md' => self::CPI_REPLY_TEXT, 'reply_to' => $ids[self::OWNER_QUESTION_INDEX]]);
            } elseif ($i === self::OWNER_QUESTION_INDEX) {
                $row = array_merge($row, ['author_type' => 'human', 'author' => E2eSeeder::NAME, 'content_md' => self::CPI_QUESTION_TEXT, 'mentions' => '{historian}']);
            } elseif ($i === self::OLDER_HISTORIAN_INDEX) {
                $row = array_merge($row, ['author' => 'historian', 'content_md' => self::OLDER_HISTORIAN_TEXT]);
            } elseif ($i % 25 === 7) {
                [$text, $mentions] = self::OWNER_POOL[$draws['owner']++ % count(self::OWNER_POOL)];
                $row = array_merge($row, ['author_type' => 'human', 'author' => E2eSeeder::NAME, 'content_md' => $text, 'mentions' => self::pgArray($mentions)]);
            } elseif ($i % 100 === 13) {
                [$severity, $title, $text] = self::ALERT_POOL[$draws['alert']++ % count(self::ALERT_POOL)];
                $row = array_merge($row, [
                    'author_type' => 'system', 'author' => 'trading_core', 'kind' => 'system_alert',
                    'card' => json_encode(['severity' => $severity, 'title' => $title]), 'content_md' => $text,
                ]);
            } elseif ($i % 50 === 11) {
                $row = array_merge($row, ['author_type' => 'system', 'author' => 'trading_core', 'content_md' => self::SYSTEM_POOL[$draws['system']++ % count(self::SYSTEM_POOL)]]);
            } elseif ($i % 100 === 17) {
                $row = array_merge($row, ['author' => 'market_scout', 'kind' => 'watchlist', 'content_md' => self::WATCHLIST_POOL[$draws['watchlist']++ % count(self::WATCHLIST_POOL)]]);
            } else {
                [$author, $text, $mentions] = self::AGENT_POOL[$draws['agent']++ % count(self::AGENT_POOL)];
                $row = array_merge($row, ['author' => $author, 'content_md' => $text, 'mentions' => self::pgArray($mentions)]);
            }
            $rows[] = $row;
        }

        foreach (array_chunk($rows, 100) as $chunk) {
            DB::table('warroom_messages')->insert($chunk);
        }
    }

    /** @param list<string> $values */
    private static function pgArray(array $values): string
    {
        return '{'.implode(',', $values).'}';
    }
}
