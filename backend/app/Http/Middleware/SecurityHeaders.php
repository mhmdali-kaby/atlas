<?php

declare(strict_types=1);

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Security headers on every response (brief §18). The API only returns JSON,
 * so its CSP allows nothing to load, run or frame it.
 */
class SecurityHeaders
{
    public const API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";

    public function handle(Request $request, Closure $next): Response
    {
        /** @var Response $response */
        $response = $next($request);

        $response->headers->set('X-Frame-Options', 'DENY');
        $response->headers->set('X-Content-Type-Options', 'nosniff');
        $response->headers->set('Referrer-Policy', 'same-origin');

        if ($request->is('api/*', 'sanctum/*')) {
            $response->headers->set('Content-Security-Policy', self::API_CSP);
        }

        // security-audit-round-4 #4: PHP sends this itself (expose_php in php.ini) before any
        // framework code runs. header_remove() operates on PHP's real pending header list, not
        // Symfony's Response object, so it is the one way to drop it short of an ini change.
        if (! headers_sent()) {
            header_remove('X-Powered-By');
        }
        $response->headers->remove('X-Powered-By');

        return $response;
    }
}
