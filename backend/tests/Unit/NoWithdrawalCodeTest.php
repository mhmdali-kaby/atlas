<?php

declare(strict_types=1);

/*
 * Brief §1.5: no withdrawal or transfer code exists anywhere in the repository.
 * This scans every source folder of the monorepo (backend, frontend, services).
 */
it('contains no OKX withdrawal or transfer endpoint anywhere in the repository', function (): void {
    $root = dirname(__DIR__, 3);
    $forbidden = ['/api/v5/asset/withdrawal', '/api/v5/asset/transfer', 'asset/withdrawal"', "asset/withdrawal'", 'asset/transfer"', "asset/transfer'"];
    $skipDirs = ['vendor', 'node_modules', '.git', 'storage', '.venv', 'dist', 'graphify-out', '__pycache__'];
    $offenders = [];

    $iterator = new RecursiveIteratorIterator(
        new RecursiveCallbackFilterIterator(
            new RecursiveDirectoryIterator($root, FilesystemIterator::SKIP_DOTS),
            fn (SplFileInfo $file): bool => ! ($file->isDir() && in_array($file->getFilename(), $skipDirs, true)),
        ),
    );

    foreach ($iterator as $file) {
        /** @var SplFileInfo $file */
        if (! in_array($file->getExtension(), ['php', 'py', 'ts', 'tsx', 'js', 'json', 'yaml', 'yml'], true)) {
            continue;
        }
        if ($file->getRealPath() === __FILE__) {
            continue;
        }
        $contents = (string) file_get_contents($file->getPathname());
        foreach ($forbidden as $needle) {
            if (str_contains($contents, $needle)) {
                $offenders[] = $file->getPathname().' contains '.$needle;
            }
        }
    }

    expect($offenders)->toBe([]);
});
