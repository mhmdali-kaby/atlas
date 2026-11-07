<?php

declare(strict_types=1);

use App\Models\User;
use Illuminate\Support\Facades\Hash;

it('creates the owner with a bcrypt password', function (): void {
    $this->artisan('atlas:create-owner')
        ->expectsQuestion('Owner name', 'Mohammed')
        ->expectsQuestion('Owner email', 'Owner@Atlas.test')
        ->expectsQuestion('Password (min 12 characters)', 'a-long-password-1')
        ->expectsQuestion('Confirm password', 'a-long-password-1')
        ->assertSuccessful();

    $owner = User::query()->sole();
    expect($owner->email)->toBe('owner@atlas.test')
        ->and($owner->locale)->toBe('en')
        ->and($owner->theme)->toBe('system')
        ->and(str_starts_with($owner->getAuthPassword(), '$2y$'))->toBeTrue()
        ->and(Hash::check('a-long-password-1', $owner->getAuthPassword()))->toBeTrue();
});

it('refuses to create a second owner', function (): void {
    User::factory()->create();

    $this->artisan('atlas:create-owner')
        ->expectsOutputToContain('An owner already exists')
        ->assertFailed();

    expect(User::query()->count())->toBe(1);
});

it('rejects a short or mismatched password', function (): void {
    $this->artisan('atlas:create-owner')
        ->expectsQuestion('Owner name', 'Mohammed')
        ->expectsQuestion('Owner email', 'owner@atlas.test')
        ->expectsQuestion('Password (min 12 characters)', 'short')
        ->expectsQuestion('Confirm password', 'different')
        ->assertFailed();

    expect(User::query()->count())->toBe(0);
});
