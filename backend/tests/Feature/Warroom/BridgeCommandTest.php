<?php

declare(strict_types=1);

use App\Support\Events\EventBus;
use Illuminate\Database\Connection;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Str;
use Symfony\Component\Process\Process;

/**
 * Runs the real `php artisan atlas:bridge` process. It writes on its own connection, outside the
 * test's RefreshDatabase transaction, so the rows it commits are read and removed through a
 * second connection.
 */
function outsideConnection(): Connection
{
    config()->set('database.connections.bridge_outside', config('database.connections.'.config('database.default')));

    return DB::connection('bridge_outside');
}

it('runs as a process, persists a published message, and exits 0 on SIGTERM', function (): void {
    foreach (EventBus::STREAMS as $stream) {
        Redis::connection('events')->command('executeRaw', [['DEL', $stream]]);
    }
    $key = 'bridge-cmd-'.Str::lower(Str::random(8));
    $outside = outsideConnection();

    $process = new Process(
        [PHP_BINARY, 'artisan', 'atlas:bridge', '--block=200'],
        base_path(),
        // Not "testing": Laravel disables signal traps while running unit tests.
        ['APP_ENV' => 'local', 'BROADCAST_CONNECTION' => 'log', 'LOG_CHANNEL' => 'stderr', 'DB_DATABASE' => config('database.connections.pgsql.database')],
    );
    $process->start();

    try {
        // Published before the group may exist: the warroom group starts at 0, so it is not missed.
        $published = app(EventBus::class)->publish('atlas.warroom', 'warroom.message', 'demo', 'agent:historian', [
            'thread' => ['kind' => 'agent', 'key' => $key],
            'author_type' => 'agent', 'author' => 'historian', 'kind' => 'text', 'content_md' => 'from the process',
        ]);

        $deadline = microtime(true) + 15;
        $count = 0;
        while (microtime(true) < $deadline && $process->isRunning()) {
            $count = $outside->table('warroom_messages')->where('event_id', $published['id'])->count();
            if ($count > 0) {
                break;
            }
            usleep(100_000);
        }
        expect($count)->toBe(1, $process->getErrorOutput().$process->getOutput());

        $process->signal(SIGTERM);
        $process->wait();

        expect($process->getExitCode())->toBe(0)
            ->and($process->getOutput())->toContain('atlas:bridge stopped');
    } finally {
        if ($process->isRunning()) {
            $process->stop(1);
        }
        $threadIds = $outside->table('warroom_threads')->where('key', $key)->pluck('id');
        $outside->table('warroom_messages')->whereIn('thread_id', $threadIds)->delete();
        $outside->table('warroom_threads')->whereIn('id', $threadIds)->delete();
        $outside->disconnect();
    }
});
