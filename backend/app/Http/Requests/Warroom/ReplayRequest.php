<?php

declare(strict_types=1);

namespace App\Http\Requests\Warroom;

use Carbon\CarbonImmutable;

/**
 * GET /api/warroom/replay?date=YYYY-MM-DD&env= — `date` is a day in the display timezone
 * (Asia/Baghdad).
 */
class ReplayRequest extends WarroomRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            ...parent::rules(),
            'date' => ['required', 'date_format:Y-m-d'],
        ];
    }

    /**
     * @return array{0: CarbonImmutable, 1: CarbonImmutable} [start, end) of the day, in UTC
     */
    public function dayWindowUtc(): array
    {
        $tz = config('atlas_defaults.timezone.display');
        $start = CarbonImmutable::createFromFormat('!Y-m-d', (string) $this->validated('date'), is_string($tz) ? $tz : 'Asia/Baghdad');
        if (! $start instanceof CarbonImmutable) {
            throw new \UnexpectedValueException('Invalid replay date.');
        }

        return [$start->utc(), $start->addDay()->utc()];
    }
}
