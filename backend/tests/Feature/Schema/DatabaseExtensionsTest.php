<?php

declare(strict_types=1);

use Illuminate\Support\Facades\DB;

it('enables the timescaledb and vector postgres extensions on migrate', function (): void {
    $names = collect(DB::select('SELECT extname FROM pg_extension'))->pluck('extname')->all();

    expect($names)->toContain('timescaledb');
    expect($names)->toContain('vector');
});
