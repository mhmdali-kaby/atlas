<?php

declare(strict_types=1);

namespace Tests\Support;

use Illuminate\Broadcasting\Broadcasters\Broadcaster;

/**
 * A fake broadcaster: records every broadcast instead of sending it to Reverb.
 */
final class RecordingBroadcaster extends Broadcaster
{
    /** @var list<array{channels: list<string>, event: string, payload: array<string, mixed>}> */
    public array $sent = [];

    public function auth($request): mixed
    {
        return null;
    }

    public function validAuthenticationResponse($request, $result): mixed
    {
        return null;
    }

    /**
     * @param  array<int, mixed>  $channels
     * @param  string  $event
     * @param  array<string, mixed>  $payload
     */
    public function broadcast(array $channels, $event, array $payload = []): void
    {
        unset($payload['socket']);
        $this->sent[] = [
            'channels' => array_values(array_map(fn ($c): string => (string) $c, $channels)),
            'event' => $event,
            'payload' => $payload,
        ];
    }

    /**
     * Install as the default broadcaster and return it.
     */
    public static function install(): self
    {
        $fake = new self;
        app('Illuminate\Broadcasting\BroadcastManager')->extend('recording', fn () => $fake);
        config()->set('broadcasting.connections.recording', ['driver' => 'recording']);
        config()->set('broadcasting.default', 'recording');

        return $fake;
    }
}
