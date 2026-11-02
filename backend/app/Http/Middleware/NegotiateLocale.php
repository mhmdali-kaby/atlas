<?php

declare(strict_types=1);

namespace App\Http\Middleware;

use App\Models\User;
use Closure;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\App;
use Symfony\Component\HttpFoundation\Response;

/**
 * ATL-222: picks the app locale from the request's Accept-Language header (brief §14.1's en|ar,
 * User::LOCALES) before anything renders, so a validation message or an error page reads in the
 * owner's language even on a request made before they are signed in. Runs before routing, so it
 * is set before a route-model-binding failure is turned into a 404 message.
 */
class NegotiateLocale
{
    public function handle(Request $request, Closure $next): Response
    {
        $locale = $request->getPreferredLanguage(User::LOCALES);
        App::setLocale(is_string($locale) ? $locale : (string) config('app.locale'));

        /** @var Response $response */
        $response = $next($request);

        return $response;
    }
}
