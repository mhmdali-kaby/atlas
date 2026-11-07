<?php

declare(strict_types=1);

/*
 * ATL-262 (security round 20, low severity): the framework registers GET and PUT /storage/{path}
 * for any 'local' disk with 'serve' => true (Illuminate\Filesystem\FilesystemServiceProvider),
 * exposing storage/app/private over HTTP with no auth of its own. config/filesystems.php now sets
 * 'serve' => false on that disk, which removes both routes rather than guarding them, so a
 * request 404s instead of being authorised or refused by the app.
 */
it('has no GET /storage/{path} route', function (): void {
    $this->get('/storage/anything')->assertNotFound();
});

it('has no PUT /storage/{path} route', function (): void {
    $this->put('/storage/anything', ['x' => 'y'])->assertNotFound();
});

it('has no GET /storage/{path} route for a nested path', function (): void {
    $this->get('/storage/private/connections.json')->assertNotFound();
});

it('keeps the local disk unserved in config', function (): void {
    expect(config('filesystems.disks.local.serve'))->toBeFalse();
});
