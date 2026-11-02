<?php

declare(strict_types=1);

namespace App\Http\Requests\Warroom;

/**
 * GET /api/warroom/messages/{message}?env= (ATL-220): one message by id, for a notification
 * link whose target is not (yet) in the loaded page.
 */
class ShowMessageRequest extends WarroomRequest {}
