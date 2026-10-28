<?php

declare(strict_types=1);

use App\Models\User;
use Database\Seeders\E2eSeeder;

it('refuses to seed a database not named atlas_e2e_*', function (): void {
    // The Pest database is atlas_test_<hash>, so the guard must refuse it.
    expect(fn () => app(E2eSeeder::class)->run())
        ->toThrow(RuntimeException::class, 'only seeds an atlas_e2e_* database');

    expect(User::query()->count())->toBe(0);
});

it('refuses to run in production', function (): void {
    $this->app['env'] = 'production';

    expect(fn () => app(E2eSeeder::class)->run())
        ->toThrow(RuntimeException::class, 'refuses to run when APP_ENV=production');
});
