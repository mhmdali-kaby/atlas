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
 * Fixture War Room for the Playwright flows (ATL-39): 500 messages in the demo Main thread, one
 * every two minutes, ending with an owner message at 2026-10-28T02:00:00Z (05:00 in Baghdad);
 * plus an empty BTC-USDT-SWAP thread and an empty private Historian thread. The mix covers every
 * renderer: agent text, owner bubbles, a reply, a system event line, a system alert and a
 * generic card. Never runs in production.
 */
class WarroomDemoSeeder extends Seeder
{
    public const COUNT = 500;

    public const LAST_AT = '2026-10-28T02:00:00Z';

    public const LAST_OWNER_TEXT = 'Hold new entries until CPI at 15:30, then send me the plan.';

    private const AGENTS = [
        'orchestrator', 'market_scout', 'news_intelligence', 'historian', 'technical_analyst',
        'derivatives_sentiment', 'strategist', 'devils_advocate', 'portfolio_manager',
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
        $rows = [];
        $ids = [];
        for ($i = 0; $i < self::COUNT; $i++) {
            $at = $last->copy()->subMinutes(2 * (self::COUNT - 1 - $i))->format('Y-m-d H:i:s.uP');
            $id = (string) Str::uuid();
            $ids[] = $id;
            $row = [
                'id' => $id, 'env' => 'demo', 'thread_id' => $main->id, 'event_id' => null,
                'author_type' => 'agent', 'author' => self::AGENTS[$i % count(self::AGENTS)], 'kind' => 'text',
                'content_md' => "Check {$i}: BTC-USDT-SWAP holds 4,120–4,180 with funding +0.01% and a stop at −0.6R.",
                'card' => null, 'reply_to' => null, 'mentions' => '{}', 'reasoning_md' => null, 'tool_calls' => null,
                'tokens_in' => null, 'tokens_out' => null, 'cost_usd' => null, 'created_at' => $at, 'updated_at' => $at,
            ];
            if ($i % 25 === 7) {
                $row = array_merge($row, ['author_type' => 'human', 'author' => E2eSeeder::NAME, 'content_md' => "@Historian what usually happens after CPI? ({$i})", 'mentions' => '{historian}']);
            } elseif ($i % 50 === 11) {
                $row = array_merge($row, ['author_type' => 'system', 'author' => 'trading_core', 'content_md' => "Synced the OKX demo account at check {$i}."]);
            } elseif ($i % 100 === 13) {
                $row = array_merge($row, [
                    'author_type' => 'system', 'author' => 'trading_core', 'kind' => 'system_alert',
                    'card' => json_encode(['severity' => 'warning', 'title' => 'Market data is late']),
                    'content_md' => 'No ticker for 42 s on XAU-USDT-SWAP. New orders wait until it is fresh.',
                ]);
            } elseif ($i % 100 === 17) {
                $row = array_merge($row, ['author' => 'market_scout', 'kind' => 'watchlist', 'content_md' => "1. **ETH-USDT-SWAP**, long, 0.61\n2. **XAU-USDT-SWAP**, short, 0.48"]);
            } elseif ($i === self::COUNT - 2) {
                // A reply to the newest owner question before it (index 482).
                $row = array_merge($row, ['author' => 'historian', 'content_md' => 'After CPI, XAU moved a median 1.4 ATR in the first hour (n = 41).', 'reply_to' => $ids[intdiv(self::COUNT - 10, 25) * 25 + 7]]);
            } elseif ($i === self::COUNT - 1) {
                $row = array_merge($row, ['author_type' => 'human', 'author' => E2eSeeder::NAME, 'content_md' => self::LAST_OWNER_TEXT]);
            }
            $rows[] = $row;
        }

        foreach (array_chunk($rows, 100) as $chunk) {
            DB::table('warroom_messages')->insert($chunk);
        }
    }
}
