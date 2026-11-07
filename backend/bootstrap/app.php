<?php

use App\Http\Middleware\NegotiateLocale;
use App\Http\Middleware\SecurityHeaders;
use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;
use Illuminate\Http\Request;
use Symfony\Component\HttpKernel\Exception\NotFoundHttpException;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        api: __DIR__.'/../routes/api.php',
        commands: __DIR__.'/../routes/console.php',
        health: '/up',
    )
    ->withMiddleware(function (Middleware $middleware): void {
        // Sanctum SPA cookie auth for requests from the stateful origins in config/sanctum.php.
        $middleware->statefulApi();
        // ATL-148: shared per-user limiter on every /api route (AppServiceProvider 'api'). The kill
        // switch routes remove it with withoutMiddleware('throttle:api').
        $middleware->throttleApi();
        // ATL-222: sets the response language from Accept-Language before anything (including
        // a route-model-binding 404) renders.
        $middleware->append(NegotiateLocale::class);
        $middleware->append(SecurityHeaders::class);
        // There is no server-rendered login page (the SPA owns it), so guests are never
        // redirected; unauthenticated API calls get a JSON 401 even without Accept: application/json.
        $middleware->redirectGuestsTo(fn () => null);
    })
    ->withExceptions(function (Exceptions $exceptions): void {
        // Exchange secrets (Connections) are never flashed back into the session (brief §1.6).
        $exceptions->dontFlash(['api_key', 'api_secret', 'passphrase', 'bot_token', 'chat_id']);
        $exceptions->shouldRenderJsonWhen(
            fn (Request $request) => $request->is('api/*') || $request->expectsJson(),
        );
        // ATL-222 (security round 16): a missing bound model (Proposal, BacktestRun, a warroom
        // message...) must read "Not found." under api/*, never the ModelNotFoundException
        // message Laravel already turned into a NotFoundHttpException ("No query results for
        // model [App\Models\Proposal] <id>") by the time render callbacks see it.
        $exceptions->render(function (NotFoundHttpException $e, Request $request) {
            if ($request->is('api/*')) {
                return response()->json(['message' => __('errors.not_found')], 404);
            }
        });
    })->create();
