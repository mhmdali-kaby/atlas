<?php

declare(strict_types=1);

it('answers the API ping', function (): void {
    $this->getJson('/api/ping')->assertOk()->assertJson(['ok' => true]);
});
