<?php

declare(strict_types=1);

use App\Models\User;
use Illuminate\Support\Str;

/**
 * ATL-222 (security round 16): a 404 for a missing bound model (Proposal, BacktestRun, ...) must
 * read "Not found." under api/*, never the ModelNotFoundException message Laravel builds by
 * default ("No query results for model [App\Models\Proposal] <id>"), which names the Eloquent
 * class to anyone who can read the response.
 */
beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
});

it('hides the model class from a 404 on an unknown proposal', function (): void {
    $id = (string) Str::uuid();

    $response = $this->getJson("/api/proposals/{$id}")
        ->assertNotFound()
        ->assertExactJson(['message' => 'Not found.']);

    expect($response->getContent())->not->toContain('App\\Models');
});

it('hides the model class from a 404 on an unknown backtest run', function (): void {
    $id = (string) Str::uuid();

    $response = $this->getJson("/api/backtests/{$id}")
        ->assertNotFound()
        ->assertExactJson(['message' => 'Not found.']);

    expect($response->getContent())->not->toContain('App\\Models');
});

it('shows the same 404 message in Arabic', function (): void {
    $id = (string) Str::uuid();

    $this->withHeaders(['Accept-Language' => 'ar'])
        ->getJson("/api/proposals/{$id}")
        ->assertNotFound()
        ->assertExactJson(['message' => 'غير موجود.']);
});
