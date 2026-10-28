<?php

declare(strict_types=1);

use App\Support\Defaults\DefaultsGenerator;
use Tests\TestCase;

uses(TestCase::class);

it('keeps both generated defaults files in step with config/defaults.yaml', function (): void {
    $stale = DefaultsGenerator::forRepository()->staleFiles();

    expect($stale)->toBe([], 'Run `cd backend && php artisan atlas:defaults` after editing config/defaults.yaml.');
});

it('exposes the brief §14.3 balanced profile as decimal strings', function (): void {
    expect(config('atlas_defaults.risk_profiles.balanced.max_loss_per_trade_pct'))->toBe('1.0')
        ->and(config('atlas_defaults.risk_profiles.balanced.min_expected_r'))->toBe('1.8')
        ->and(config('atlas_defaults.risk_profiles.conservative.max_loss_per_trade_pct'))->toBe('0.5')
        ->and(config('atlas_defaults.risk_profiles.aggressive.max_leverage'))->toBe(10)
        ->and(config('atlas_defaults.autonomy.default_level'))->toBe(2)
        ->and(config('atlas_defaults.autonomy.on_approval_timeout'))->toBe('reject')
        ->and(config('atlas_defaults.env.default'))->toBe('demo')
        ->and(config('atlas_defaults.okx.environments.demo.headers.x-simulated-trading'))->toBe('1');
});

it('contains no float anywhere in the generated PHP defaults', function (): void {
    $floats = [];
    $defaults = require config_path('atlas_defaults.php');
    array_walk_recursive(
        $defaults,
        function (mixed $v, int|string $k) use (&$floats): void {
            if (is_float($v)) {
                $floats[] = $k;
            }
        },
    );

    expect($floats)->toBe([]);
});

it('refuses to generate when the YAML holds an unquoted float', function (): void {
    DefaultsGenerator::forRepository()->render("risk:\n  max_loss_per_trade_pct: 1.0\n");
})->throws(InvalidArgumentException::class, 'risk.max_loss_per_trade_pct');

it('detects a YAML edit that was not regenerated', function (): void {
    $dir = sys_get_temp_dir().'/atlas-defaults-'.bin2hex(random_bytes(4));
    mkdir($dir);
    $yaml = $dir.'/defaults.yaml';
    copy(DefaultsGenerator::forRepository()->yamlPath(), $yaml);
    $generator = new DefaultsGenerator($yaml, $dir.'/atlas_defaults.php', $dir.'/defaults.py');
    $generator->write();

    expect($generator->staleFiles())->toBe([]);

    file_put_contents($yaml, str_replace('max_loss_per_trade_pct: "1.0"', 'max_loss_per_trade_pct: "1.5"', (string) file_get_contents($yaml)));

    expect($generator->staleFiles())->toHaveCount(2);

    array_map('unlink', glob($dir.'/*') ?: []);
    rmdir($dir);
});
