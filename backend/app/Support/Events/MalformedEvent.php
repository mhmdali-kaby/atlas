<?php

declare(strict_types=1);

namespace App\Support\Events;

/**
 * An entry the bridge can never process (bad JSON, a missing or invalid field). The message
 * names what is wrong (field names only), never the entry's contents, so it is safe to log.
 */
final class MalformedEvent extends \RuntimeException {}
