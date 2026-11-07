<?php

declare(strict_types=1);

// ATL-222 (security round 16): a missing bound model's 404 never names its Eloquent class or id.
return [
    'not_found' => 'Not found.',
];
