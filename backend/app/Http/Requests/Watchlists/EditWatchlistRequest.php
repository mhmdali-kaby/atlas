<?php

declare(strict_types=1);

namespace App\Http\Requests\Watchlists;

use App\Models\Watchlist;
use Illuminate\Foundation\Http\FormRequest;

/**
 * Base for the owner's watchlist writes (remove, restore, pin). The {watchlist} route model is
 * authorised through WatchlistPolicy::update.
 */
class EditWatchlistRequest extends FormRequest
{
    public function authorize(): bool
    {
        $user = $this->user();

        return $user !== null && $user->can('update', $this->watchlist());
    }

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [];
    }

    public function watchlist(): Watchlist
    {
        $watchlist = $this->route('watchlist');
        if (! $watchlist instanceof Watchlist) {
            throw new \UnexpectedValueException('Route has no {watchlist}.');
        }

        return $watchlist;
    }
}
