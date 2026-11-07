<?php

declare(strict_types=1);

use App\Http\Middleware\SecurityHeaders;
use Illuminate\Http\Request;
use Illuminate\Http\Response;

/**
 * security-audit-round-4 #4: PHP adds X-Powered-By itself (expose_php), which never reaches
 * Symfony's Response header bag, so a Pest HTTP test can never see it either way — confirmed with
 * a real `php -S` request in evidence/ATL-52. This test covers the one part of the fix that *is*
 * observable in-process: a response that already carries the header (set upstream, or by a future
 * SAPI/library change) must not leave this middleware with it still attached.
 */
it('strips X-Powered-By from the response even if something upstream set it', function (): void {
    $middleware = new SecurityHeaders;
    $request = Request::create('/api/ping', 'GET');

    $response = $middleware->handle($request, function () {
        $response = new Response('ok');
        $response->headers->set('X-Powered-By', 'PHP/8.5.10');

        return $response;
    });

    expect($response->headers->has('X-Powered-By'))->toBeFalse()
        ->and($response->headers->get('X-Frame-Options'))->toBe('DENY');
});
