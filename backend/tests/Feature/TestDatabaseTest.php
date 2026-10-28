<?php

declare(strict_types=1);

use Illuminate\Support\Facades\DB;
use Tests\Support\TestDatabase;

it('runs against this checkout\'s own test database', function () {
    $expected = getenv('DB_TEST_DATABASE') ?: TestDatabase::derivedName();

    expect(TestDatabase::name())->toBe($expected)
        ->and(config('database.connections.pgsql.database'))->toBe($expected)
        ->and(DB::selectOne('select current_database() as name')->name)->toBe($expected);
});

it('derives atlas_test_ plus the first 8 hex of sha1 of the repository root', function () {
    expect(TestDatabase::derivedName('/Users/owner/atlas'))
        ->toBe('atlas_test_'.substr(sha1('/Users/owner/atlas'), 0, 8))
        ->and(TestDatabase::derivedName())->toMatch('/^atlas_test_[0-9a-f]{8}$/')
        ->and(TestDatabase::derivedName('/a'))->not->toBe(TestDatabase::derivedName('/b'))
        ->and(TestDatabase::repoRoot())->toBe(realpath(base_path('..')));
});
