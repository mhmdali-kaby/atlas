<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Support\Doctor\CheckResult;
use App\Support\Doctor\Doctor;
use Illuminate\Console\Attributes\Description;
use Illuminate\Console\Attributes\Signature;
use Illuminate\Console\Command;

/**
 * App-level health checks for `./atlas doctor` (brief §18). One line per check, ✓ pass, ⚠ warning,
 * ✗ failure; exits 1 when any required check fails. `--json` prints a JSON array of
 * {name, ok, status, detail}. Output never contains a key, secret or full UID.
 */
#[Signature('atlas:doctor {--json : Print the checks as a JSON array}')]
#[Description('Check that Postgres, Redis, trading_core, OKX and the demo key are healthy')]
class AtlasDoctor extends Command
{
    private const SYMBOLS = [CheckResult::PASS => '✓', CheckResult::WARN => '⚠', CheckResult::FAIL => '✗'];

    public function handle(Doctor $doctor): int
    {
        $results = $doctor->run();
        $failed = count(array_filter($results, static fn (CheckResult $r): bool => ! $r->ok()));

        if ($this->option('json') === true) {
            $this->line(json_encode(
                array_map(static fn (CheckResult $r): array => $r->toArray(), $results),
                JSON_THROW_ON_ERROR | JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE,
            ));

            return $failed === 0 ? self::SUCCESS : self::FAILURE;
        }

        $width = max(0, ...array_map(static fn (CheckResult $r): int => mb_strlen($r->name), $results));
        foreach ($results as $r) {
            $this->line(sprintf('%s %s  %s', self::SYMBOLS[$r->status], str_pad($r->name, $width), $r->detail));
        }
        $this->newLine();
        $this->line($failed === 0
            ? (string) __('doctor.summary_ok')
            : (string) __('doctor.summary_failed', ['count' => $failed]));

        return $failed === 0 ? self::SUCCESS : self::FAILURE;
    }
}
