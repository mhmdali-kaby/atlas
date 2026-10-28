<?php

declare(strict_types=1);

use App\Logging\DropExceptionTraceArgsProcessor;
use Illuminate\Support\Facades\Crypt;
use Illuminate\Support\Facades\Log;
use Tests\TestCase;

uses(TestCase::class);

/**
 * ATL-33 AC3: the default log channel never writes an exception's argument list.
 */
it('logs no argument list for an exception thrown with a secret as an argument', function (): void {
    $logFile = storage_path('logs/atl33-trace-test-'.bin2hex(random_bytes(4)).'.log');
    config([
        'logging.default' => 'atl33',
        'logging.channels.atl33' => [
            'driver' => 'single',
            'path' => $logFile,
            'level' => 'debug',
            'tap' => [DropExceptionTraceArgsProcessor::class],
        ],
    ]);

    // Force the argument back on for this one exception, so the test proves the *processor*
    // strips it, not just that zend.exception_ignore_args (AppServiceProvider::register()) did.
    $previous = ini_set('zend.exception_ignore_args', '0');
    try {
        try {
            Crypt::decryptString('not-a-real-ciphertext-xyz123');
            $this->fail('Expected decryptString to throw.');
        } catch (Throwable $e) {
            // Sanity check: the exception itself really did capture the argument (PHP truncates
            // long string args in getTraceAsString() to their first 15 characters), otherwise
            // this test would pass for the wrong reason.
            expect($e->getTraceAsString())->toContain('not-a-real-ciph');
            Log::error('decrypt failed', ['exception' => $e]);
        }
    } finally {
        ini_set('zend.exception_ignore_args', $previous === false ? '1' : $previous);
    }

    try {
        $log = (string) file_get_contents($logFile);
        expect($log)->toContain('decrypt failed')
            ->and($log)->toContain('DecryptException')
            ->and($log)->not->toContain('not-a-real-ciph')
            ->and($log)->not->toContain("decryptString('");
    } finally {
        @unlink($logFile);
    }
});

it('sets zend.exception_ignore_args so PHP never captures the arguments in the first place', function (): void {
    expect(ini_get('zend.exception_ignore_args'))->toBe('1');
});
