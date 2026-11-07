<?php

declare(strict_types=1);

namespace App\Support\Doctor;

/**
 * One `atlas:doctor` line. `status` is pass | warn | fail; only `fail` makes the command exit non-zero.
 * `detail` is shown to the owner and must never contain a key, secret, passphrase or full UID.
 */
final readonly class CheckResult
{
    public const PASS = 'pass';

    public const WARN = 'warn';

    public const FAIL = 'fail';

    private function __construct(
        public string $name,
        public string $status,
        public string $detail,
    ) {}

    public static function pass(string $name, string $detail): self
    {
        return new self($name, self::PASS, $detail);
    }

    public static function warn(string $name, string $detail): self
    {
        return new self($name, self::WARN, $detail);
    }

    public static function fail(string $name, string $detail): self
    {
        return new self($name, self::FAIL, $detail);
    }

    public function ok(): bool
    {
        return $this->status !== self::FAIL;
    }

    /**
     * @return array{name: string, ok: bool, status: string, detail: string}
     */
    public function toArray(): array
    {
        return ['name' => $this->name, 'ok' => $this->ok(), 'status' => $this->status, 'detail' => $this->detail];
    }
}
