<?php

declare(strict_types=1);

namespace App\Logging;

use Illuminate\Log\Logger;
use Monolog\Logger as MonologLogger;
use Monolog\LogRecord;
use Throwable;

/**
 * 'tap' processor for the default log channel (config/logging.php `single`), ATL-33 AC3.
 *
 * An exception's trace can carry a decrypted ciphertext, a passphrase or another secret as a
 * function argument (e.g. Crypt::decryptString($ciphertext) failing with $ciphertext still in
 * the frame). This replaces any Throwable found in a log record's context with a description
 * built from file:line + class/function per frame, never from the frame's argument list, so a
 * secret fragment cannot reach storage/logs/*.log through that path — independent of whether
 * zend.exception_ignore_args (set in AppServiceProvider::register()) applied to that particular
 * exception.
 */
final class DropExceptionTraceArgsProcessor
{
    public function __invoke(Logger $logger): void
    {
        $underlying = $logger->getLogger();
        if (! $underlying instanceof MonologLogger) {
            // Every Laravel log driver this app configures (single, daily) wraps a Monolog
            // logger; this guard only protects against a future driver that does not.
            return;
        }

        $underlying->pushProcessor(function (LogRecord $record): LogRecord {
            $context = $record->context;
            $changed = false;
            foreach ($context as $key => $value) {
                if ($value instanceof Throwable) {
                    $context[$key] = $this->describe($value);
                    $changed = true;
                }
            }

            return $changed ? $record->with(context: $context) : $record;
        });
    }

    /**
     * @return array<string, mixed>
     */
    private function describe(Throwable $e): array
    {
        return [
            'class' => $e::class,
            'message' => $e->getMessage(),
            'file' => $e->getFile().':'.$e->getLine(),
            'trace' => $this->framesWithoutArgs($e),
            'previous' => $e->getPrevious() !== null ? $this->describe($e->getPrevious()) : null,
        ];
    }

    /**
     * @return list<string>
     */
    private function framesWithoutArgs(Throwable $e): array
    {
        return array_map(
            static function (array $frame): string {
                $location = ($frame['file'] ?? '{internal}').':'.($frame['line'] ?? 0);
                $call = ($frame['class'] ?? '').($frame['type'] ?? '').$frame['function'];

                // Deliberately never reads $frame['args']: that is the whole point of this class.
                return $call === '' ? $location : "{$location} {$call}()";
            },
            $e->getTrace(),
        );
    }
}
