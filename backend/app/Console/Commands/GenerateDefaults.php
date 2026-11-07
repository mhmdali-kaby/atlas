<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Support\Defaults\DefaultsGenerator;
use Illuminate\Console\Command;
use InvalidArgumentException;

final class GenerateDefaults extends Command
{
    protected $signature = 'atlas:defaults {--check : Exit non-zero if a generated file is stale, without writing}';

    protected $description = 'Generate backend/config/atlas_defaults.php and atlas_shared/defaults.py from config/defaults.yaml';

    public function handle(): int
    {
        $generator = DefaultsGenerator::forRepository();

        try {
            if ($this->option('check')) {
                $stale = $generator->staleFiles();
                foreach ($stale as $path) {
                    $this->error("Out of date: {$path}");
                }

                return $stale === [] ? self::SUCCESS : self::FAILURE;
            }

            $generator->write();
        } catch (InvalidArgumentException $e) {
            $this->error($e->getMessage());

            return self::FAILURE;
        }

        $this->info('Wrote '.$generator->phpPath());
        $this->info('Wrote '.$generator->pythonPath());

        return self::SUCCESS;
    }
}
