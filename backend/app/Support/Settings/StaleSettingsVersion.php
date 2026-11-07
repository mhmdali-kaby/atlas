<?php

declare(strict_types=1);

namespace App\Support\Settings;

use RuntimeException;

/**
 * The PATCH was based on a version that is no longer current (someone else saved first).
 */
final class StaleSettingsVersion extends RuntimeException
{
    public function __construct(public readonly int $currentVersion, public readonly int $givenVersion)
    {
        parent::__construct("Settings changed since version {$givenVersion}; the current version is {$currentVersion}.");
    }
}
