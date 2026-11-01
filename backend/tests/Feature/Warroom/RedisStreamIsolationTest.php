<?php

declare(strict_types=1);

/**
 * ATL-211: no Pest test may touch an unprefixed `atlas.*` stream key directly — every test must
 * go through App\Support\Events\EventBus::streamKey()/streamKeys(), which applies this checkout's
 * Redis prefix (empty in production). This is the PHP mirror of
 * services/shared/tests/test_redis_isolation.py::test_no_python_test_touches_an_unprefixed_stream.
 *
 * Two shapes are flagged, matching what this codebase actually sends to Redis:
 *   1. a raw command array, e.g. ['XADD', 'atlas.warroom', ...] or ['DEL', $stream], passed to
 *      Redis::connection('events')->command('executeRaw', [...]) or a local redisRaw()/xadd() test
 *      helper built on it;
 *   2. a direct call to one of StreamReader's own stream-taking methods (ensureGroup, readNew,
 *      claimPending, ack, pendingIds) with a literal stream name or a bare "$stream"-ish variable.
 * In both cases the stream argument is flagged only when it is a literal `atlas.*` string or a
 * bare variable whose name contains "stream" — not when it is wrapped in a call expression (i.e.
 * EventBus::streamKey($stream) is never flagged, because the argument there is a StaticCall node,
 * not a bare String_ or Variable).
 */

use App\Support\Events\EventBus;
use PhpParser\Error;
use PhpParser\Node;
use PhpParser\Node\Expr\Array_;
use PhpParser\Node\Expr\FuncCall;
use PhpParser\Node\Expr\MethodCall;
use PhpParser\Node\Expr\StaticCall;
use PhpParser\Node\Expr\Variable;
use PhpParser\Node\Scalar\String_;
use PhpParser\NodeTraverser;
use PhpParser\NodeVisitorAbstract;
use PhpParser\ParserFactory;

const ATL211_RAW_COMMANDS = [
    'XADD', 'XRANGE', 'XREVRANGE', 'XLEN', 'XDEL', 'XREAD', 'XTRIM', 'XINFO',
    'XGROUP', 'XREADGROUP', 'XACK', 'XAUTOCLAIM', 'XPENDING', 'DEL', 'EXISTS',
];

const ATL211_STREAM_READER_METHODS = ['ensureGroup', 'readNew', 'claimPending', 'ack', 'pendingIds'];

/** A literal `atlas.*` stream name, or a bare "...stream..." variable, used directly as a key. */
function atl211StreamLike(Node $node): bool
{
    if ($node instanceof String_) {
        return in_array($node->value, EventBus::STREAMS, true);
    }
    if ($node instanceof Variable) {
        return is_string($node->name) && str_contains(strtolower($node->name), 'stream');
    }

    return false;
}

/** @return list<int> line numbers where an unprefixed stream name reaches a Redis key argument */
function atl211UnprefixedStreamUses(string $source): array
{
    $parser = (new ParserFactory)->createForNewestSupportedVersion();

    try {
        $ast = $parser->parse($source);
    } catch (Error) {
        return [];
    }
    if ($ast === null) {
        return [];
    }

    $lines = [];
    $visitor = new class($lines) extends NodeVisitorAbstract
    {
        public function __construct(private array &$lines) {}

        public function enterNode(Node $node)
        {
            if ($node instanceof Array_) {
                $items = $node->items;
                if (isset($items[0]) && $items[0] !== null && $items[0]->value instanceof String_
                    && in_array($items[0]->value->value, ATL211_RAW_COMMANDS, true)
                ) {
                    foreach (array_slice($items, 1) as $item) {
                        if ($item !== null && atl211StreamLike($item->value)) {
                            $this->lines[] = $node->getStartLine();

                            break;
                        }
                    }
                }

                return null;
            }

            $name = match (true) {
                $node instanceof MethodCall && $node->name instanceof Node\Identifier => $node->name->toString(),
                $node instanceof StaticCall && $node->name instanceof Node\Identifier => $node->name->toString(),
                $node instanceof FuncCall && $node->name instanceof Node\Name => $node->name->toString(),
                default => null,
            };
            if ($name === null || ! in_array($name, ATL211_STREAM_READER_METHODS, true)) {
                return null;
            }

            foreach ($node->getArgs() as $arg) {
                $value = $arg->value;
                $flagged = $value instanceof Array_
                    ? array_any($value->items, fn ($item) => $item !== null && atl211StreamLike($item->value))
                    : atl211StreamLike($value);
                if ($flagged) {
                    $this->lines[] = $node->getStartLine();

                    break;
                }
            }

            return null;
        }
    };

    $traverser = new NodeTraverser;
    $traverser->addVisitor($visitor);
    $traverser->traverse($ast);

    sort($lines);

    return array_values(array_unique($lines));
}

if (! function_exists('array_any')) {
    function array_any(array $items, callable $predicate): bool
    {
        foreach ($items as $item) {
            if ($predicate($item)) {
                return true;
            }
        }

        return false;
    }
}

it('flags a raw command array or a StreamReader call carrying an unprefixed stream, and nothing else', function (): void {
    $bad = <<<'PHP'
    <?php
    $a = redisRaw(['XADD', 'atlas.warroom', '*', 'envelope', $json]);
    $b = Redis::connection('events')->command('executeRaw', [['DEL', $stream]]);
    $c = app(StreamReader::class)->pendingIds('atlas.warroom', Bridge::GROUP);
    $d = app(StreamReader::class)->readNew(Bridge::GROUP, 'c', ['atlas.warroom'], 10, 10);
    PHP;
    expect(atl211UnprefixedStreamUses($bad))->toBe([2, 3, 4, 5]);

    $good = <<<'PHP'
    <?php
    $a = redisRaw(['XADD', EventBus::streamKey('atlas.warroom'), '*', 'envelope', $json]);
    $b = Redis::connection('events')->command('executeRaw', [['DEL', EventBus::streamKey($stream)]]);
    $c = app(StreamReader::class)->pendingIds(EventBus::streamKey('atlas.warroom'), Bridge::GROUP);
    $e = app(BridgeEventHandler::class)->handle('atlas.warroom', $raw);
    $f = Log::info('atlas.bridge.started', ['consumer' => $consumer]);
    PHP;
    expect(atl211UnprefixedStreamUses($good))->toBe([]);
});

/** @return list<string> every .php file under $dir, however deeply nested */
function atl211PhpFiles(string $dir): array
{
    $iterator = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($dir, FilesystemIterator::SKIP_DOTS),
    );
    $files = [];
    foreach ($iterator as $file) {
        if ($file->isFile() && $file->getExtension() === 'php') {
            $files[] = $file->getPathname();
        }
    }
    sort($files);

    return $files;
}

it('finds no test touching an unprefixed atlas.* stream key', function (): void {
    $testsDir = dirname(__DIR__, 2);
    $files = atl211PhpFiles($testsDir);
    expect(count($files))->toBeGreaterThan(50);

    $base = $testsDir.'/';
    $offenders = [];
    foreach ($files as $file) {
        $source = file_get_contents($file);
        if ($source === false) {
            continue;
        }
        foreach (atl211UnprefixedStreamUses($source) as $line) {
            $offenders[] = str_replace($base, '', $file).':'.$line;
        }
    }

    expect($offenders)->toBe([]);
});
