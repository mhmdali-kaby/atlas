<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Support\Events\Bridge;
use Illuminate\Console\Attributes\Description;
use Illuminate\Console\Attributes\Signature;
use Illuminate\Console\Command;

/**
 * Long-running: Redis Streams (atlas.*) → Postgres → Reverb (brief §3.2). See App\Support\Events\Bridge.
 * SIGTERM or SIGINT finishes the current event and exits 0.
 */
#[Signature('atlas:bridge
    {--consumer=bridge-1 : Consumer name inside the "bridge" group}
    {--block=1000 : Milliseconds each XREADGROUP waits for new entries}')]
#[Description('Persist War Room stream events and broadcast bus events to the browser over Reverb')]
class AtlasBridge extends Command
{
    public function handle(Bridge $bridge): int
    {
        $consumer = $this->option('consumer');
        $block = $this->option('block');
        if (! is_string($consumer) || preg_match('/^[A-Za-z0-9_.:-]{1,64}$/', $consumer) !== 1) {
            $this->error('--consumer must be 1-64 characters of A-Z a-z 0-9 _ . : -');

            return self::INVALID;
        }
        if (! is_string($block) || ! ctype_digit($block) || (int) $block < 1) {
            $this->error('--block must be a positive number of milliseconds');

            return self::INVALID;
        }

        $this->trap([SIGTERM, SIGINT], fn () => $bridge->stop());

        $this->info("atlas:bridge running as {$consumer} (group ".Bridge::GROUP.')');
        $bridge->run($consumer, (int) $block);
        $this->info('atlas:bridge stopped');

        return self::SUCCESS;
    }
}
