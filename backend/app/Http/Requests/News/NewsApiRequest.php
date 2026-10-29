<?php

declare(strict_types=1);

namespace App\Http\Requests\News;

use App\Rules\IsoDateTime;
use Carbon\CarbonImmutable;
use Illuminate\Foundation\Http\FormRequest;

/**
 * Base for the read-only news, calendar and sentiment requests (ATL-67). News is not
 * env-specific, so there is no ?env=.
 */
abstract class NewsApiRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    public function limit(): int
    {
        $limit = $this->validated('limit');

        return is_numeric($limit) ? (int) $limit : self::intDefault('page_limit_default', 50);
    }

    protected function dateInput(string $key): ?CarbonImmutable
    {
        return IsoDateTime::parse($this->validated($key));
    }

    protected static function intDefault(string $key, int $fallback): int
    {
        $value = config("atlas_defaults.news.api.{$key}");

        return is_int($value) ? $value : $fallback;
    }

    protected static function stringDefault(string $key, string $fallback): string
    {
        $value = config("atlas_defaults.news.api.{$key}");

        return is_string($value) && $value !== '' ? $value : $fallback;
    }

    /**
     * @return list<string>
     */
    protected static function limitRule(): array
    {
        return ['sometimes', 'integer', 'min:1', 'max:'.self::intDefault('page_limit_max', 100)];
    }
}
