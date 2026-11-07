<?php

declare(strict_types=1);

namespace Database\Seeders;

use Carbon\CarbonImmutable;
use Database\Seeders\Concerns\GuardsRealInstall;
use Illuminate\Database\Seeder;
use Illuminate\Support\Facades\DB;
use Ramsey\Uuid\Uuid;

/**
 * Realistic research content for a demo install (ATL-260): the News feed, the economic and earnings
 * calendar, the Journal's post-mortems, the Agents scorecards and today's agent usage.
 *
 * It tells the same story as WarroomDemoSeeder: the day before US CPI, with the team watching
 * BTC, ETH, XAU, TSLA, NVDA and SPY perps for owner Mohammed Al-Kaabi. Every time is relative to
 * now, like PortfolioDemoSeeder: news within the last 48 h, CPI, FOMC minutes and NVDA earnings
 * within the next 48 h, 14 days of scores ending today (Asia/Baghdad). Some headlines and two
 * post-mortems are in Arabic.
 *
 * Re-running replaces only this seeder's rows: news, calendar and journal rows have fixed UUIDs
 * (uuid5 of a key), score days carry data.demo = true, and today's runs carry the correlation id
 * CORRELATION. Env is demo. Never runs in production.
 */
class ResearchDemoSeeder extends Seeder
{
    use GuardsRealInstall;

    public const ENV = 'demo';

    public const SCORE_DAYS = 14;

    public const CORRELATION = 'research-demo';

    private const NS = 'b6f1c3a2-4e7d-5a8b-9c0d-1e2f3a4b5c6d';

    private const TZ = 'Asia/Baghdad';

    /**
     * News items: [key, source, url path on the source's domain, title, language, instruments,
     * asset_classes, event_type, importance, sentiment, hours ago, cluster key|null, summary].
     *
     * @var list<array{0: string, 1: string, 2: string, 3: string, 4: string, 5: list<string>, 6: list<string>, 7: string, 8: int, 9: string, 10: int, 11: string|null, 12: string}>
     */
    private const NEWS = [
        ['cpi-cnbc', 'rss.cnbc', 'https://www.cnbc.com/{y}/{m}/{d}/cpi-preview-economists-expect-0point3percent-monthly-rise.html',
            'CPI preview: economists expect a 0.3% monthly rise as tariffs feed into goods prices', 'en',
            ['SPY-USDT-SWAP', 'XAU-USDT-SWAP', 'BTC-USDT-SWAP'], ['etf', 'commodity', 'crypto'], 'macro_release', 4, '-0.1000', 10, 'cpi',
            'Consensus sees headline CPI up 0.3% m/m after 0.4% the month before, with core also at 0.3%.'],
        ['cpi-marketwatch', 'rss.marketwatch', 'https://www.marketwatch.com/story/traders-brace-for-cpi-as-treasury-yields-hold-near-two-week-high-{y}{m}{d}',
            'Traders brace for CPI as Treasury yields hold near a two-week high', 'en',
            ['SPY-USDT-SWAP', 'XAU-USDT-SWAP'], ['etf', 'commodity'], 'macro_release', 4, '-0.1500', 8, 'cpi',
            'The 10-year yield sits at 4.21% ahead of the inflation report; options price a 1.1% move in the S&P 500.'],
        ['cpi-asharq', 'rss.asharq_business', 'https://asharqbusiness.com/أسواق/{y}/{m}/{d}/الأسواق-تترقب-بيانات-التضخم-الأمريكية',
            'الأسواق تترقب بيانات التضخم الأمريكية وسط توقعات بارتفاع 0.3% على أساس شهري', 'ar',
            ['SPY-USDT-SWAP', 'XAU-USDT-SWAP', 'BTC-USDT-SWAP'], ['etf', 'commodity', 'crypto'], 'macro_release', 4, '-0.1000', 6, 'cpi',
            'يتوقع المحللون أن يرتفع مؤشر أسعار المستهلكين 0.3% على أساس شهري بعد 0.4% في الشهر السابق.'],
        ['gold-marketwatch', 'rss.marketwatch', 'https://www.marketwatch.com/story/gold-holds-above-4050-as-central-bank-buying-offsets-firmer-dollar-{y}{m}{d}',
            'Gold holds above $4,050 as central-bank buying offsets a firmer dollar', 'en',
            ['XAU-USDT-SWAP'], ['commodity'], 'central_bank', 3, '0.3500', 14, 'gold',
            'Spot gold traded at $4,056 an ounce; the dollar index rose 0.2% to 99.4.'],
        ['gold-finnhub', 'finnhub.news', 'https://finnhub.io/api/news?id=8f2c41d07a95b6e3',
            'Central banks added 64 tonnes of gold in September, World Gold Council says', 'en',
            ['XAU-USDT-SWAP'], ['commodity'], 'central_bank', 3, '0.4000', 12, 'gold',
            'Poland, Turkey and China led net purchases, the council said in its monthly update.'],
        ['gold-alarabiya', 'rss.alarabiya', 'https://www.alarabiya.net/aswaq/commodities/{y}/{m}/{d}/الذهب-يتماسك-فوق-4050-دولارا',
            'الذهب يتماسك فوق 4050 دولاراً مع استمرار مشتريات البنوك المركزية', 'ar',
            ['XAU-USDT-SWAP'], ['commodity'], 'central_bank', 3, '0.3500', 11, 'gold',
            'أضافت البنوك المركزية 64 طناً من الذهب إلى احتياطياتها في سبتمبر بحسب مجلس الذهب العالمي.'],
        ['eth-etf-coindesk', 'rss.coindesk', 'https://www.coindesk.com/markets/{y}/{m}/{d}/spot-ether-etfs-draw-312m-in-a-day-the-most-since-august',
            'Spot ether ETFs draw $312 million in a day, the most since August', 'en',
            ['ETH-USDT-SWAP'], ['crypto'], 'ETF_flow', 3, '0.5000', 20, 'eth-etf',
            'Net inflows across US spot ether funds reached $312 million, led by BlackRock\'s ETHA.'],
        ['eth-etf-theblock', 'rss.theblock', 'https://www.theblock.co/post/381245/ether-etf-inflows-jump-blackrock-leads',
            'Ether ETF inflows jump to $312 million as BlackRock\'s fund leads', 'en',
            ['ETH-USDT-SWAP'], ['crypto'], 'ETF_flow', 3, '0.5000', 19, 'eth-etf',
            'ETHA took in $198 million of the day\'s total; Fidelity\'s FETH added $61 million.'],
        ['eth-etf-cointelegraph', 'rss.cointelegraph', 'https://cointelegraph.com/news/eth-etfs-biggest-daily-inflow-in-ten-weeks',
            'ETH ETFs see their biggest daily inflow in ten weeks', 'en',
            ['ETH-USDT-SWAP'], ['crypto'], 'ETF_flow', 3, '0.4500', 18, 'eth-etf',
            'Ether traded near $3,990 as fund demand returned ahead of the US inflation print.'],
        ['tsla-cnbc', 'rss.cnbc', 'https://www.cnbc.com/{y}/{m}/{d}/tesla-expands-robotaxi-pilot-to-austin-highways.html',
            'Tesla expands its robotaxi pilot to Austin highways; shares edge higher after hours', 'en',
            ['TSLA-USDT-SWAP'], ['stock'], 'product', 3, '0.3000', 30, 'tsla-robotaxi',
            'The service now covers highway routes to the airport; shares rose 1.2% in extended trading.'],
        ['tsla-yahoo', 'rss.yahoo', 'https://finance.yahoo.com/news/tesla-widens-robotaxi-service-area-austin-regulators-ask-incident-data-{y}{m}{d}.html',
            'Tesla widens robotaxi service area in Austin as regulators ask for incident data', 'en',
            ['TSLA-USDT-SWAP'], ['stock'], 'product', 3, '0.0500', 28, 'tsla-robotaxi',
            'NHTSA asked Tesla for details of two low-speed incidents in the pilot zone.'],
        ['nvda-finnhub', 'finnhub.news', 'https://finnhub.io/api/news?id=c71e9a3b52d04f18',
            'Nvidia faces a new US review of China-bound chip export licences, Bloomberg reports', 'en',
            ['NVDA-USDT-SWAP'], ['stock'], 'regulation', 4, '-0.4500', 9, 'nvda-export',
            'The Commerce Department is reviewing licences granted for a cut-down data-centre chip, according to people familiar.'],
        ['nvda-asharq', 'rss.asharq_business', 'https://asharqbusiness.com/شركات/{y}/{m}/{d}/إنفيديا-تواجه-مراجعة-أمريكية-لتراخيص-تصدير-الرقائق',
            'إنفيديا تواجه مراجعة أمريكية جديدة لتراخيص تصدير الرقائق إلى الصين', 'ar',
            ['NVDA-USDT-SWAP'], ['stock'], 'regulation', 4, '-0.4500', 8, 'nvda-export',
            'تراجع سهم إنفيديا 1.8% في التداولات المسائية بعد تقرير عن مراجعة تراخيص التصدير.'],
        ['btc-coindesk', 'rss.coindesk', 'https://www.coindesk.com/markets/{y}/{m}/{d}/bitcoin-slips-below-113000-as-traders-cut-leverage-before-cpi',
            'Bitcoin slips below $113,000 as traders cut leverage before CPI', 'en',
            ['BTC-USDT-SWAP'], ['crypto'], 'other', 3, '-0.3000', 4, 'btc-cpi',
            'Open interest on BTC perpetuals fell 2.1% over four hours while funding stayed near neutral.'],
        ['btc-alarabiya', 'rss.alarabiya', 'https://www.alarabiya.net/aswaq/crypto/{y}/{m}/{d}/بيتكوين-تتراجع-دون-113-ألف-دولار',
            'بيتكوين تتراجع دون 113 ألف دولار قبيل بيانات التضخم الأمريكية', 'ar',
            ['BTC-USDT-SWAP'], ['crypto'], 'other', 3, '-0.3000', 3, 'btc-cpi',
            'قلّص المتداولون مراكزهم ذات الرافعة المالية قبل صدور بيانات أسعار المستهلكين.'],
        ['btc-etf-theblock', 'rss.theblock', 'https://www.theblock.co/post/381102/us-spot-bitcoin-etfs-net-outflows-187-million',
            'US spot bitcoin ETFs post $187 million of net outflows', 'en',
            ['BTC-USDT-SWAP'], ['crypto'], 'ETF_flow', 3, '-0.2500', 15, null,
            'Outflows were concentrated in Fidelity\'s FBTC and Grayscale\'s GBTC; IBIT was flat.'],
        ['nvda-yahoo', 'rss.yahoo', 'https://finance.yahoo.com/news/nvidia-earnings-preview-data-centre-revenue-expected-near-54-billion-{y}{m}{d}.html',
            'Nvidia earnings preview: data-centre revenue expected near $54 billion', 'en',
            ['NVDA-USDT-SWAP'], ['stock'], 'earnings', 3, '0.1500', 16, null,
            'Analysts expect EPS of $1.26 on revenue of $54.6 billion; options imply a 7% move.'],
        ['spy-cnbc', 'rss.cnbc', 'https://www.cnbc.com/{y}/{m}/{d}/sp-500-closes-at-record-as-megacap-tech-rallies-into-inflation-report.html',
            'S&P 500 closes at a record as megacap tech rallies into the inflation report', 'en',
            ['SPY-USDT-SWAP'], ['etf'], 'other', 2, '0.3500', 24, null,
            'The index gained 0.6% to a record close; the Nasdaq 100 rose 0.9%.'],
        ['oil-marketwatch', 'rss.marketwatch', 'https://www.marketwatch.com/story/oil-falls-as-opec-signals-another-output-increase-in-december-{y}{m}{d}',
            'Oil falls 1.6% as OPEC+ signals another output increase in December', 'en',
            ['CL-USDT-SWAP'], ['commodity'], 'supply_shock', 3, '-0.4000', 22, null,
            'WTI crude settled at $60.84 a barrel after delegates said a 137,000 barrel-a-day increase is likely.'],
        ['sol-decrypt', 'rss.decrypt', 'https://decrypt.co/349812/solana-network-upgrade-goes-live-validators-report-faster-blocks',
            'Solana network upgrade goes live; validators report faster block times', 'en',
            ['SOL-USDT-SWAP'], ['crypto'], 'product', 2, '0.3000', 26, null,
            'Median block time fell to 380 ms in the first hours after activation.'],
        ['eth-gas-cointelegraph', 'rss.cointelegraph', 'https://cointelegraph.com/news/ethereum-validators-approve-gas-limit-increase',
            'Ethereum validators approve a gas-limit increase as ETH holds $3,980', 'en',
            ['ETH-USDT-SWAP'], ['crypto'], 'product', 2, '0.2000', 34, null,
            'More than half of validators signalled for a 60 million gas limit.'],
        ['okx-maintenance', 'okx.announcements', 'https://www.okx.com/help/okx-scheduled-system-upgrade-{y}{m}{d}',
            'OKX scheduled system upgrade: trading continues, deposits paused for 30 minutes', 'en',
            [], ['crypto'], 'other', 3, '0.0000', 40, null,
            'Deposits and withdrawals on some networks pause for 30 minutes; spot and derivatives trading are unaffected.'],
    ];

    /**
     * Post-mortems: [key, inst_id, side, asset_class, outcome, realised_r, pnl_usd, risk_usd, days ago,
     * entry, stop, take profits, avg_exit, mfe_r, mae_r, holding minutes, close_reason, setup, regime,
     * catalyst, agents, language-specific text].
     *
     * @return list<array<string, mixed>>
     */
    private static function journal(): array
    {
        return [
            [
                'key' => 'xau-long', 'inst' => 'XAU-USDT-SWAP', 'side' => 'long', 'asset' => 'commodity', 'outcome' => 'win',
                'r' => '1.90', 'pnl' => '1874.60', 'risk' => '1000.00', 'fees' => '-21.40', 'funding' => '-4.00', 'days' => 2,
                'entry' => '4012.00', 'stop' => '3984.00', 'tps' => [['4068.00', '50'], ['4096.00', '30'], ['4124.00', '20']],
                'exp_r' => '2.6', 'p' => '0.56', 'avg_entry' => '4012.40', 'exit' => '4065.60', 'mfe' => '2.40', 'mae' => '-0.35', 'hold' => 610,
                'reason' => 'take_profit', 'setup' => 'pullback_to_support', 'regime' => 'high_vol', 'catalyst' => 'central_bank',
                'agents' => ['technical_analyst', 'historian', 'strategist', 'portfolio_manager'], 'da' => 2,
                'title' => 'XAU-USDT-SWAP long: +1.90R +$1,874.60 (win)',
                'invalidation' => '1H close below 3,984',
                'expected_md' => 'Long gold on a pullback to the 4,008–4,016 support zone (strength 0.72), stop 3,984 below the zone plus 0.25 ATR. TP1 4,068 at 2R, final target 4,124. Probability of TP1 before stop 0.56.',
                'actual_md' => 'Filled at 4,012.40. TP1 hit after 7 h, the stop moved to breakeven at +1R, and the rest trailed out at 4,065.60 on the 1H swing low. Realised +1.90R against a stated 2.6R.',
                'lessons_md' => 'When central-bank buying and a strong 4H level agree, take TP1 in full and trail the rest on the 1H swing rather than a fixed ATR.',
                'right' => '**Technical Analyst** picked the 4,008–4,016 zone that held twice. **Historian** flagged the high-vol range regime, which kept the size at 2.5%.',
                'wrong' => '**Strategist** set TP3 at 4,124, beyond the 20-day high; it was never close.',
                'votes' => [['technical_analyst', 'take', '0.70', 'Strong level and trend alignment on 4H.'], ['devils_advocate', 'take', '0.60', 'Stop outside 4h noise; no event inside the window.']],
            ],
            [
                'key' => 'btc-breakout', 'inst' => 'BTC-USDT-SWAP', 'side' => 'long', 'asset' => 'crypto', 'outcome' => 'loss',
                'r' => '-1.02', 'pnl' => '-1021.80', 'risk' => '1000.00', 'fees' => '-18.20', 'funding' => '-3.60', 'days' => 4,
                'entry' => '114950.0', 'stop' => '113400.0', 'tps' => [['118050.0', '50'], ['119600.0', '50']],
                'exp_r' => '2.5', 'p' => '0.48', 'avg_entry' => '114985.0', 'exit' => '113380.0', 'mfe' => '0.30', 'mae' => '-1.03', 'hold' => 185,
                'reason' => 'stop_loss', 'setup' => 'breakout', 'regime' => 'range', 'catalyst' => 'none',
                'agents' => ['technical_analyst', 'derivatives_sentiment', 'strategist', 'devils_advocate'], 'da' => 3,
                'title' => 'BTC-USDT-SWAP long: −1.02R −$1,021.80 (loss)',
                'invalidation' => '1H close back inside the 112,400–114,900 range',
                'expected_md' => 'Breakout long on a 1H close above 114,900 with relative volume 1.4. Stop 113,400 under the breakout bar. TP1 118,050 at 2R. Probability 0.48.',
                'actual_md' => 'Filled at 114,985 on the breakout bar. Price failed back inside the range within two hours and the stop filled at 113,380. A failed breakout.',
                'lessons_md' => 'Do not take range breakouts in a range regime with flat open interest; wait for a retest of the broken level.',
                'right' => '**Devil\'s Advocate** said open interest was falling into the breakout (−2.1% in 4h), a sign of short covering, not new longs.',
                'wrong' => '**Technical Analyst** counted volume confirmation from one bar. **Strategist** gave 0.48 against an analog base rate of 0.38.',
                'votes' => [['technical_analyst', 'take', '0.65', 'Breakout with relative volume above 1.3.'], ['devils_advocate', 'change', '0.55', 'Wait for a retest; OI is not confirming.']],
            ],
            [
                'key' => 'tsla-short', 'inst' => 'TSLA-USDT-SWAP', 'side' => 'short', 'asset' => 'stock', 'outcome' => 'loss',
                'r' => '-1.08', 'pnl' => '-538.90', 'risk' => '500.00', 'fees' => '-7.10', 'funding' => '1.20', 'days' => 6,
                'entry' => '441.20', 'stop' => '447.80', 'tps' => [['428.00', '60'], ['421.40', '40']],
                'exp_r' => '2.3', 'p' => '0.50', 'avg_entry' => '441.05', 'exit' => '448.20', 'mfe' => '0.45', 'mae' => '-1.08', 'hold' => 940,
                'reason' => 'stop_loss', 'setup' => 'rally_to_resistance', 'regime' => 'trend', 'catalyst' => 'product',
                'agents' => ['technical_analyst', 'news_intelligence', 'strategist', 'devils_advocate'], 'da' => 4,
                'title' => 'TSLA-USDT-SWAP بيع: −1.08R −$538.90 (خسارة)',
                'invalidation' => 'إغلاق ساعة فوق 447.80',
                'expected_md' => 'بيع تسلا عند مقاومة 440–442 (قوة 0.64) مع وقف خسارة عند 447.80. الهدف الأول 428 بعائد 2R. احتمال الوصول إلى الهدف قبل الوقف 0.50.',
                'actual_md' => 'تم التنفيذ عند 441.05. خبر توسيع خدمة الروبوتاكسي صدر بعد إغلاق السوق الأمريكي، فقفز السعر في الجلسة الليلية ونُفّذ الوقف عند 448.20 بانزلاق 0.40 دولار.',
                'lessons_md' => 'لا تحتفظ بمركز بيع على أسهم العقود الدائمة خلال الليل حين يكون هناك خبر شركة متوقع؛ السيولة الليلية ضعيفة والفجوات تتجاوز الوقف.',
                'right' => 'نبّه **محامي الشيطان** إلى خطر الفجوة بعد إغلاق السوق وأعطى تقييم مخاطرة 4.',
                'wrong' => 'لم يأخذ **الاستراتيجي** تحذير **استخبارات الأخبار** عن خبر الروبوتاكسي في الحسبان، وأبقى الحجم كاملاً.',
                'votes' => [['technical_analyst', 'take', '0.60', 'Rally into a 0.64-strength resistance zone.'], ['devils_advocate', 'skip', '0.70', 'Overnight gap risk on a pending company headline.']],
            ],
            [
                'key' => 'eth-long', 'inst' => 'ETH-USDT-SWAP', 'side' => 'long', 'asset' => 'crypto', 'outcome' => 'win',
                'r' => '1.42', 'pnl' => '1398.30', 'risk' => '1000.00', 'fees' => '-16.50', 'funding' => '-5.20', 'days' => 8,
                'entry' => '3948.00', 'stop' => '3902.00', 'tps' => [['4040.00', '50'], ['4086.00', '50']],
                'exp_r' => '2.5', 'p' => '0.54', 'avg_entry' => '3949.20', 'exit' => '4014.50', 'mfe' => '2.05', 'mae' => '-0.40', 'hold' => 1320,
                'reason' => 'time_stop', 'setup' => 'pullback_to_support', 'regime' => 'trend', 'catalyst' => 'ETF_flow',
                'agents' => ['news_intelligence', 'derivatives_sentiment', 'strategist', 'portfolio_manager'], 'da' => 2,
                'title' => 'ETH-USDT-SWAP long: +1.42R +$1,398.30 (win)',
                'invalidation' => '4H close below 3,902',
                'expected_md' => 'Long ether on a pullback to 3,936–3,958 after record ETF inflows. Stop 3,902, TP1 4,040 at 2R. Probability 0.54. Time stop 24 h.',
                'actual_md' => 'Filled at 3,949.20. Price reached +2.05R but stalled 12 below TP1. The 24 h time stop closed it at 4,014.50 for +1.42R.',
                'lessons_md' => 'When ETF flows drive the move, place TP1 just under the round number (4,000) rather than at the next zone.',
                'right' => '**News Intelligence** labelled the ETF inflows CONFIRMED from two outlets. **Derivatives & Sentiment** saw new longs, not covering.',
                'wrong' => '**Strategist** set TP1 at 4,040, above the 4,000 round number where sellers waited.',
                'votes' => [['news_intelligence', 'take', '0.70', 'Confirmed flows, not yet priced in (0.6 ATR since first seen).'], ['devils_advocate', 'take', '0.55', 'Correlation with the BTC long is 0.86; size reduced.']],
            ],
            [
                'key' => 'spy-long', 'inst' => 'SPY-USDT-SWAP', 'side' => 'long', 'asset' => 'etf', 'outcome' => 'win',
                'r' => '2.31', 'pnl' => '1152.40', 'risk' => '500.00', 'fees' => '-9.80', 'funding' => '-2.80', 'days' => 10,
                'entry' => '672.40', 'stop' => '668.10', 'tps' => [['681.00', '50'], ['685.30', '50']],
                'exp_r' => '2.5', 'p' => '0.58', 'avg_entry' => '672.50', 'exit' => '682.42', 'mfe' => '2.90', 'mae' => '-0.20', 'hold' => 2210,
                'reason' => 'take_profit', 'setup' => 'breakout', 'regime' => 'trend', 'catalyst' => 'earnings',
                'agents' => ['technical_analyst', 'historian', 'strategist', 'portfolio_manager'], 'da' => 1,
                'title' => 'SPY-USDT-SWAP long: +2.31R +$1,152.40 (win)',
                'invalidation' => '1H close below 668.10',
                'expected_md' => 'Breakout and retest of 672 during megacap earnings week, EMA 20/50/200 aligned on 4H. Stop 668.10, TP1 681 at 2R. Probability 0.58.',
                'actual_md' => 'Filled at 672.50 on the retest. TP1 hit on the second US session, the remainder trailed at 1.0 ATR(1H) and closed at 685.30 for a blended +2.31R.',
                'lessons_md' => 'A breakout and retest in a trending regime with aligned EMAs is worth full size; trail at 1.0 ATR after +2R.',
                'right' => '**Historian**: breakout-retest in this regime held 61% of the time (n = 44). **Portfolio Manager** tightened the trail at +2R as the rules say.',
                'wrong' => 'Nothing material; entry could have been a post-only limit to save 3 bps of fees.',
                'votes' => [['historian', 'take', '0.65', 'History supports: 61% hit rate, n = 44.'], ['devils_advocate', 'take', '0.70', 'Rating 1: no event inside the window.']],
            ],
            [
                'key' => 'nvda-short', 'inst' => 'NVDA-USDT-SWAP', 'side' => 'short', 'asset' => 'stock', 'outcome' => 'loss',
                'r' => '-0.58', 'pnl' => '-291.70', 'risk' => '500.00', 'fees' => '-6.40', 'funding' => '0.90', 'days' => 12,
                'entry' => '192.80', 'stop' => '195.60', 'tps' => [['187.20', '60'], ['184.40', '40']],
                'exp_r' => '2.4', 'p' => '0.47', 'avg_entry' => '192.75', 'exit' => '194.38', 'mfe' => '0.35', 'mae' => '-0.82', 'hold' => 2880,
                'reason' => 'time_stop', 'setup' => 'range_fade', 'regime' => 'range', 'catalyst' => 'regulation',
                'agents' => ['technical_analyst', 'news_intelligence', 'strategist', 'portfolio_manager'], 'da' => 3,
                'title' => 'NVDA-USDT-SWAP بيع: −0.58R −$291.70 (خسارة)',
                'invalidation' => 'إغلاق ساعة فوق 195.60',
                'expected_md' => 'بيع إنفيديا عند الحد الأعلى للنطاق 192–193.40 بعد تقرير عن تشديد قيود التصدير. الوقف 195.60 والهدف الأول 187.20 بعائد 2R. الاحتمال 0.47.',
                'actual_md' => 'تم التنفيذ عند 192.75. بقي السعر داخل النطاق جلستين أمريكيتين دون أن يصل إلى الهدف أو الوقف، فأغلق مدير المحفظة المركز بوقف الوقت عند 194.38.',
                'lessons_md' => 'خبر منفرد من مصدر واحد (REPORTED) لا يكفي لكسر نطاق قبل نتائج الأرباح؛ انتظر التأكيد أو صدور النتائج.',
                'right' => 'طبّق **مدير المحفظة** قاعدة وقف الوقت في موعدها، فبقيت الخسارة أقل من 1R.',
                'wrong' => 'اعتمد **الاستراتيجي** على خبر لم يؤكده إلا مصدر واحد، و**المحلل الفني** وصف النطاق بأنه ضعيف رغم ثلاث لمسات.',
                'votes' => [['news_intelligence', 'take', '0.50', 'REPORTED by one outlet; partly priced in.'], ['devils_advocate', 'change', '0.60', 'Wait for a second source or for earnings.']],
            ],
        ];
    }

    public function run(): void
    {
        $this->guardAgainstRealInstall('ResearchDemoSeeder');

        $now = CarbonImmutable::now()->utc()->startOfMinute();

        DB::transaction(function () use ($now): void {
            $this->news($now);
            $this->calendar($now);
            $this->journalEntries($now);
            $this->scores($now);
            $this->runs($now);
        });
    }

    /** Fixed UUID for one of this seeder's rows. */
    public static function id(string $key): string
    {
        return Uuid::uuid5(self::NS, 'research-demo:'.$key)->toString();
    }

    /** @return list<string> */
    public static function newsIds(): array
    {
        return array_map(static fn (array $n): string => self::id('news:'.$n[0]), self::NEWS);
    }

    /** @return list<string> */
    public static function journalIds(): array
    {
        return array_map(static fn (array $j): string => self::id('journal:'.$j['key']), self::journal());
    }

    private function news(CarbonImmutable $now): void
    {
        $date = $now->subDay();
        $urls = [];
        $items = [];
        $clusters = [];
        foreach (self::NEWS as [$key, $source, $url, $title, $lang, $instruments, $classes, $type, $importance, $sentiment, $hoursAgo, $cluster, $summary]) {
            $url = strtr($url, ['{y}' => $date->format('Y'), '{m}' => $date->format('m'), '{d}' => $date->format('d')]);
            $urls[] = $url;
            $published = $now->subMinutes($hoursAgo * 60);
            $clusterId = $cluster === null ? null : self::id('cluster:'.$cluster);
            $items[] = [
                'id' => self::id('news:'.$key), 'source' => $source, 'url' => $url, 'canonical_url' => $url,
                'title' => $title, 'title_original' => $lang === 'en' ? null : $title, 'summary' => $summary, 'body' => null,
                'published_at' => self::t($published), 'fetched_at' => self::t($published->addSeconds(40)),
                'language' => $lang, 'entities' => null, 'event_type' => $type, 'sentiment' => $sentiment,
                'importance' => $importance, 'novelty' => null, 'cluster_id' => $clusterId, 'raw' => null,
                'instruments' => self::pgArray($instruments), 'asset_classes' => self::pgArray($classes),
                'created_at' => self::t($published), 'updated_at' => self::t($published),
            ];
            if ($cluster !== null) {
                $c = $clusters[$cluster] ?? ['id' => $clusterId, 'items' => []];
                $c['items'][] = ['title' => $title, 'at' => $published, 'importance' => $importance, 'instruments' => $instruments];
                $clusters[$cluster] = $c;
            }
        }

        // The first item of a story is novel; the later ones repeat it.
        $seen = [];
        foreach ($items as &$item) {
            $story = $item['cluster_id'] ?? $item['id'];
            $item['novelty'] = isset($seen[$story]) ? '0.0800' : '0.7400';
            $seen[$story] = true;
        }
        unset($item);

        DB::table('news_items')->whereIn('id', self::newsIds())->delete();
        DB::table('news_items')->whereIn('canonical_url', $urls)->delete();
        DB::table('news_clusters')->whereIn('id', array_column($clusters, 'id'))->delete();

        $clusterRows = [];
        foreach ($clusters as $c) {
            usort($c['items'], static fn (array $a, array $b): int => $a['at'] <=> $b['at']);
            $first = $c['items'][0];
            $last = $c['items'][count($c['items']) - 1];
            $instruments = array_values(array_unique(array_merge(...array_column($c['items'], 'instruments'))));
            $clusterRows[] = [
                'id' => $c['id'], 'headline' => $first['title'], 'first_seen' => self::t($first['at']), 'last_seen' => self::t($last['at']),
                'item_count' => count($c['items']), 'max_importance' => max(array_column($c['items'], 'importance')),
                'instruments' => self::pgArray($instruments), 'alerted_at' => null,
                'created_at' => self::t($first['at']), 'updated_at' => self::t($last['at']),
            ];
        }
        DB::table('news_clusters')->insert($clusterRows);
        DB::table('news_items')->insert($items);
    }

    private function calendar(CarbonImmutable $now): void
    {
        // CPI at the next 12:30 UTC at least two hours away; FOMC minutes at the next 19:00 UTC.
        $cpi = $now->setTime(12, 30);
        while ($cpi->lessThan($now->addHours(2))) {
            $cpi = $cpi->addDay();
        }
        $minutes = $now->setTime(19, 0);
        while ($minutes->lessThan($now->addHour())) {
            $minutes = $minutes->addDay();
        }
        $claims = $cpi;

        $events = [
            ['us-cpi-mm', 'US', 'CPI m/m', 5, $cpi, '0.3%', '0.4%'],
            ['us-core-cpi-mm', 'US', 'Core CPI m/m', 5, $cpi, '0.3%', '0.3%'],
            ['us-cpi-yy', 'US', 'CPI y/y', 4, $cpi, '3.0%', '2.9%'],
            ['us-jobless-claims', 'US', 'Unemployment Claims', 3, $claims, '226K', '231K'],
            ['us-fomc-minutes', 'US', 'FOMC Meeting Minutes', 4, $minutes, null, null],
            ['us-crude-inventories', 'US', 'Crude Oil Inventories', 3, $minutes->subHours(4)->subMinutes(30), '-1.2M', '2.0M'],
        ];

        $rows = [];
        foreach ($events as [$key, $country, $name, $importance, $at, $forecast, $previous]) {
            $rows[] = [
                'id' => self::id('event:'.$key), 'source' => 'forexfactory.calendar', 'external_id' => 'atlas-demo:'.$key,
                'country' => $country, 'name' => $name, 'importance' => $importance, 'scheduled_at' => self::t($at),
                'actual' => null, 'forecast' => $forecast, 'previous' => $previous, 'surprise' => null,
                'created_at' => self::t($now), 'updated_at' => self::t($now),
            ];
        }
        DB::table('economic_events')->upsert($rows, ['source', 'external_id']);

        // NVDA reports after the US close: the next US/Eastern date whose 16:00 is still ahead.
        $ny = $now->setTimezone('America/New_York');
        $day = $ny->setTime(16, 0)->greaterThan($ny->addHour()) ? $ny : $ny->addDay();
        DB::table('earnings_events')->where('id', self::id('earnings:NVDA'))->delete();
        DB::table('earnings_events')->upsert([[
            'id' => self::id('earnings:NVDA'), 'symbol' => 'NVDA', 'date' => $day->toDateString(), 'time' => 'amc',
            'eps_est' => '1.26', 'eps_act' => null, 'rev_est' => '54600000000', 'rev_act' => null,
            'created_at' => self::t($now), 'updated_at' => self::t($now),
        ]], ['symbol', 'date'], ['time', 'eps_est', 'eps_act', 'rev_est', 'rev_act', 'updated_at']);
    }

    private function journalEntries(CarbonImmutable $now): void
    {
        DB::table('journal_entries')->whereIn('id', self::journalIds())->delete();

        $rows = [];
        foreach (self::journal() as $j) {
            $closed = $now->subDays($j['days'])->setTime(13, 40);
            $opened = $closed->subMinutes($j['hold']);
            $written = $closed->addMinutes(6);
            $data = [
                'side' => $j['side'],
                'asset_class' => $j['asset'],
                'outcome' => $j['outcome'],
                'realised_r' => $j['r'],
                'expected' => [
                    'entry' => $j['entry'], 'entry_zone' => null, 'stop' => $j['stop'],
                    'take_profits' => array_map(static fn (array $tp): array => ['price' => $tp[0], 'pct' => $tp[1]], $j['tps']),
                    'expected_r' => $j['exp_r'], 'probability' => $j['p'], 'invalidation' => $j['invalidation'],
                    'time_stop_minutes' => $j['asset'] === 'crypto' || $j['asset'] === 'commodity' ? 1440 : 2880,
                    'author' => 'strategist',
                ],
                'actual' => [
                    'avg_entry' => $j['avg_entry'], 'avg_exit' => $j['exit'], 'fees_usd' => $j['fees'], 'funding_usd' => $j['funding'],
                    'realised_pnl_usd' => $j['pnl'], 'initial_risk_usd' => $j['risk'], 'realised_r' => $j['r'],
                    'mfe_r' => $j['mfe'], 'mae_r' => $j['mae'],
                    'opened_at' => $opened->toIso8601ZuluString(), 'closed_at' => $closed->toIso8601ZuluString(),
                    'holding_minutes' => $j['hold'], 'close_reason' => $j['reason'],
                ],
                'votes' => array_map(static fn (array $v): array => ['agent' => $v[0], 'vote' => $v[1], 'confidence' => $v[2], 'reason' => $v[3]], $j['votes']),
                'agents' => $j['agents'],
                'review' => ['right_md' => $j['right'], 'wrong_md' => $j['wrong']],
                'lesson' => $j['lessons_md'],
                'lesson_pending' => false,
                'lesson_attempts' => 1,
                'da_rating' => $j['da'],
                'position_ref' => self::id('position:'.$j['key']).':'.$closed->toIso8601ZuluString(),
            ];
            $rows[] = [
                'id' => self::id('journal:'.$j['key']), 'env' => self::ENV, 'agent' => 'portfolio_manager', 'inst_id' => $j['inst'],
                'proposal_id' => self::id('proposal:'.$j['key']), 'title' => $j['title'],
                'expected_md' => $j['expected_md'], 'actual_md' => $j['actual_md'], 'lessons_md' => $j['lessons_md'],
                'data' => json_encode($data, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE),
                'tags' => self::pgArray([
                    'setup:'.$j['setup'], 'asset:'.$j['asset'], 'regime:'.$j['regime'], 'catalyst:'.$j['catalyst'],
                    'outcome:'.$j['outcome'], $j['outcome'],
                ]),
                'created_at' => self::t($written), 'updated_at' => self::t($written),
            ];
        }
        DB::table('journal_entries')->insert($rows);
    }

    /**
     * 14 Baghdad days of scores for every roster agent, ending today, with the data keys each
     * scorer writes (atlas_agents.scoring; brief §7 Scoring).
     */
    private function scores(CarbonImmutable $now): void
    {
        DB::table('agent_scores')->where('env', self::ENV)->whereRaw("data->>'demo' = 'true'")->delete();

        $today = $now->setTimezone(self::TZ)->startOfDay();
        $rows = [];
        foreach (self::agents() as $agent) {
            $seed = crc32('research-demo:'.$agent);
            $rand = static function (int $lo, int $hi) use (&$seed): int {
                $seed = ($seed * 1103515245 + 12345) % 2147483648;

                return $lo + ($seed % ($hi - $lo + 1));
            };
            for ($i = self::SCORE_DAYS - 1; $i >= 0; $i--) {
                $day = $today->subDays($i)->toDateString();
                [$n, $hit, $brier, $data] = self::scoreDay($agent, $rand);
                $data['demo'] = true;
                $rows[] = [
                    'id' => self::id("score:{$agent}:{$day}"), 'agent' => $agent, 'env' => self::ENV, 'day' => $day,
                    'n' => $n, 'hit_rate' => $hit, 'brier_score' => $brier, 'calibration' => '{}', 'pnl_attributed_usd' => null,
                    'data' => json_encode($data, JSON_THROW_ON_ERROR),
                    'created_at' => self::t($now), 'updated_at' => self::t($now),
                ];
            }
        }
        DB::table('agent_scores')->upsert($rows, ['agent', 'env', 'day'], ['n', 'hit_rate', 'brier_score', 'calibration', 'data', 'updated_at']);
    }

    /**
     * One agent's day: [n, hit_rate, brier_score, data].
     *
     * @param  callable(int, int): int  $rand
     * @return array{0: int, 1: string|null, 2: string|null, 3: array<string, mixed>}
     */
    private static function scoreDay(string $agent, callable $rand): array
    {
        $rate = static fn (int $k, int $n): string => number_format($k / max(1, $n), 4, '.', '');
        $dec = static fn (int $v, int $scale): string => number_format($v / (10 ** $scale), $scale, '.', '');
        $weight = static fn (): string => $dec($rand(90, 125), 2);

        switch ($agent) {
            case 'market_scout':
                $n = $rand(3, 5);
                $k = $rand(max(1, $n - 3), $n);

                return [$n, $rate($k, $n), null, [
                    'selection_edge_atr' => $dec($rand(8, 62), 2), 'directional_accuracy' => $rate($k, $n),
                    'directional_n' => $n, 'directional_correct' => $k,
                ]];
            case 'news_intelligence':
                $n = $rand(2, 7);

                return [$n, null, null, [
                    'latency_p90_s' => $rand(48, 84), 'latency_median_s' => $rand(22, 41), 'reliability_weight' => $weight(),
                ]];
            case 'historian':
                $n = $rand(6, 10);
                $mix = ['supports' => $rand(0, 2), 'weakly_supports' => $rand(1, 4), 'does_not_support' => $rand(1, 3), 'contradicts' => $rand(0, 1)];
                $brier = $dec($rand(165, 225), 3);

                return [$n, null, $brier, [
                    'brier_n' => $n, 'mean_p' => $dec($rand(52, 60), 2), 'observed' => $dec($rand(48, 58), 2),
                    'verdict_mix' => $mix, 'verdicts_n' => array_sum($mix), 'reliability_weight' => $weight(),
                ]];
            case 'technical_analyst':
                $tbsN = $rand(2, 4);
                $tbsK = $rand(0, $tbsN);
                $lvN = $rand(4, 7);
                $nsN = $rand(2, 5);

                return [$tbsN + $nsN, $rate($tbsK, $tbsN), null, [
                    'target_before_stop_rate' => $rate($tbsK, $tbsN), 'target_before_stop_n' => $tbsN, 'target_hits' => $tbsK,
                    'level_hold_rate' => $rate($rand(max(0, $lvN - 3), $lvN), $lvN), 'level_hold_n' => $lvN,
                    'avg_realised_r' => $dec($rand(-40, 160), 2), 'realised_r_n' => $tbsN,
                    'avg_expected_r' => $dec($rand(180, 260), 2), 'expected_r_n' => $tbsN,
                    'no_setup_accuracy' => $rate($rand(max(0, $nsN - 2), $nsN), $nsN), 'no_setup_n' => $nsN,
                    'reliability_weight' => $weight(),
                ]];
            case 'derivatives_sentiment':
                $sN = $rand(4, 7);
                $sK = $rand(max(0, $sN - 4), $sN);
                $qN = $rand(0, 2);
                $data = [
                    'supports_accuracy' => $rate($sK, $sN), 'supports_n' => $sN, 'supports_correct' => $sK,
                    'squeeze_calls_n' => $qN, 'brier_n' => $sN, 'mean_p' => $dec($rand(30, 42), 2), 'observed' => $dec($rand(26, 40), 2),
                    'reliability_weight' => $weight(),
                ];
                if ($qN > 0) {
                    $qK = $rand(0, $qN);
                    $data += ['squeeze_call_hit_rate' => $rate($qK, $qN), 'squeeze_call_hits' => $qK];
                }

                return [$sN, $rate($sK, $sN), $dec($rand(180, 235), 3), $data];
            case 'strategist':
                $sub = $rand(2, 5);
                $rej = $rand(0, 100) < 15 ? 1 : 0;
                $taken = $rand(1, max(1, $sub - $rej));
                $won = $rand(0, $taken);

                return [$sub, $rate($won, $taken), $dec($rand(190, 240), 3), [
                    'avg_realised_r' => $dec($rand(-60, 170), 2), 'taken_n' => $taken,
                    'rejection_rate' => $rate($rej, $sub), 'submitted_n' => $sub, 'risk_rejected_n' => $rej,
                    'brier_n' => $sub, 'mean_p' => $dec($rand(50, 58), 2), 'observed' => $dec($rand(42, 56), 2),
                    'regret_r' => $dec($rand(-20, 45), 2), 'regret_n' => $rand(1, 3),
                ]];
            case 'devils_advocate':
                $high = $rand(0, 2);
                $low = $rand(1, 3);
                $data = ['high_n' => $high, 'low_n' => $low, 'reliability_weight' => $weight()];
                $highWon = $high > 0 ? $rand(0, min(1, $high)) : 0;
                $lowLost = $rand(0, min(1, $low));
                if ($high > 0) {
                    $data += ['false_alarm_rate' => $rate($highWon, $high), 'high_won' => $highWon];
                }
                $data += ['miss_rate' => $rate($lowLost, $low), 'low_lost' => $lowLost];

                return [$high + $low, null, null, $data];
            case 'portfolio_manager':
                $closed = $rand(1, 3);
                $losers = $rand(0, $closed);
                $data = [
                    'r_captured' => $dec($rand(52, 78), 2), 'r_captured_n' => $closed,
                    'losers_n' => $losers, 'compliance_rate' => '1.0000', 'compliance_n' => $closed * 12 + $rand(10, 40),
                ];
                $data['compliance_ok'] = $data['compliance_n'];
                if ($losers > 0) {
                    $within = $rand(max(0, $losers - 1), $losers);
                    $data += ['losers_within_rate' => $rate($within, $losers), 'losers_within_n' => $within];
                }

                return [$closed, null, null, $data];
            default: // orchestrator
                $cycles = $rand(28, 30);
                $onTime = $cycles - $rand(0, 2);
                $ack = $rand(5, 12);

                return [$cycles, $rate($onTime, $cycles), null, [
                    'on_time_rate' => $rate($onTime, $cycles), 'cycles_n' => $cycles, 'cycles_on_time' => $onTime,
                    'ack_p90_seconds' => $dec($rand(41, 86), 1), 'ack_median_seconds' => $dec($rand(18, 35), 1), 'ack_n' => $ack,
                    'loops_stopped' => $rand(0, 2),
                ]];
        }
    }

    /** Today's runs per agent (Asia/Baghdad day), so the Agents page shows tokens and turns. */
    private function runs(CarbonImmutable $now): void
    {
        DB::table('agent_runs')->where('env', self::ENV)->where('correlation_id', self::CORRELATION)->delete();

        $start = $now->setTimezone(self::TZ)->startOfDay()->utc();
        $elapsed = max(60, $start->diffInSeconds($now, true));
        $strong = ['market_scout', 'news_intelligence', 'historian', 'strategist', 'devils_advocate', 'portfolio_manager'];
        $rows = [];
        foreach (self::agents() as $a => $agent) {
            $count = 2 + ($a % 4);
            for ($r = 1; $r <= $count; $r++) {
                $startedAt = $start->addSeconds((int) floor($elapsed * ($r * 10 + $a) / (($count + 1) * 10 + 9)));
                $endedAt = $startedAt->addSeconds(min(35 + 9 * $a + 7 * $r, max(1, (int) $startedAt->diffInSeconds($now, true))));
                $rows[] = [
                    'id' => self::id("run:{$agent}:{$r}"), 'env' => self::ENV, 'agent' => $agent,
                    'trigger' => $r === 1 ? 'schedule' : ($r % 2 === 0 ? 'mention' : 'schedule'),
                    'correlation_id' => self::CORRELATION,
                    'started_at' => self::t($startedAt), 'ended_at' => self::t($endedAt), 'status' => 'ok',
                    'model' => in_array($agent, $strong, true) ? 'opus' : 'sonnet', 'prompt_version' => null,
                    'tokens_in' => 6200 + 1350 * $a + 830 * $r, 'tokens_out' => 640 + 115 * $a + 90 * $r,
                    'turns' => 2 + (($a + $r) % 5), 'cost_usd_notional' => number_format((6200 + 1350 * $a + 830 * $r) * 0.000004, 6, '.', ''),
                    'error' => null, 'created_at' => self::t($startedAt), 'updated_at' => self::t($endedAt),
                ];
            }
        }
        DB::table('agent_runs')->insert($rows);
    }

    /** @return list<string> */
    private static function agents(): array
    {
        $roster = config('atlas_defaults.agents.roster');

        return is_array($roster) ? array_map(strval(...), array_keys($roster)) : [];
    }

    private static function t(CarbonImmutable $at): string
    {
        return $at->utc()->format('Y-m-d H:i:s.uP');
    }

    /** @param list<string> $values */
    private static function pgArray(array $values): string
    {
        return '{'.implode(',', array_map(static fn (string $v): string => '"'.addcslashes($v, '"\\').'"', $values)).'}';
    }
}
