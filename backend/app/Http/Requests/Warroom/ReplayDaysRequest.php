<?php

declare(strict_types=1);

namespace App\Http\Requests\Warroom;

use Carbon\CarbonImmutable;

/**
 * GET /api/warroom/replay/days?to=YYYY-MM-DD&days=7&env= — the message count of each of the
 * `days` Baghdad days ending with `to` (ATL-207: the Replay sidebar's "Recent days").
 */
class ReplayDaysRequest extends WarroomRequest
{
    public const MAX_DAYS = 31;

    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            ...parent::rules(),
            'to' => ['required', 'date_format:Y-m-d'],
            'days' => ['sometimes', 'integer', 'min:1', 'max:'.self::MAX_DAYS],
        ];
    }

    public function timezone(): string
    {
        $tz = config('atlas_defaults.timezone.display');

        return is_string($tz) ? $tz : 'Asia/Baghdad';
    }

    public function dayCount(): int
    {
        return (int) $this->validated('days', 7);
    }

    /**
     * @return list<string> the requested days, newest first (Y-m-d in the display timezone)
     */
    public function dayList(): array
    {
        $to = CarbonImmutable::createFromFormat('!Y-m-d', (string) $this->validated('to'), $this->timezone());
        if (! $to instanceof CarbonImmutable) {
            throw new \UnexpectedValueException('Invalid replay date.');
        }
        $days = [];
        for ($i = 0; $i < $this->dayCount(); $i++) {
            $days[] = $to->subDays($i)->format('Y-m-d');
        }

        return $days;
    }

    /**
     * @return array{0: CarbonImmutable, 1: CarbonImmutable} [start of the oldest day, end of `to`), in UTC
     */
    public function windowUtc(): array
    {
        $days = $this->dayList();
        $tz = $this->timezone();
        $first = CarbonImmutable::createFromFormat('!Y-m-d', $days[count($days) - 1], $tz);
        $last = CarbonImmutable::createFromFormat('!Y-m-d', $days[0], $tz);
        if (! $first instanceof CarbonImmutable || ! $last instanceof CarbonImmutable) {
            throw new \UnexpectedValueException('Invalid replay date.');
        }

        return [$first->utc(), $last->addDay()->utc()];
    }
}
