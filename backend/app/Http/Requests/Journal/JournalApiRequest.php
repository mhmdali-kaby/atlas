<?php

declare(strict_types=1);

namespace App\Http\Requests\Journal;

use Illuminate\Foundation\Http\FormRequest;

/**
 * Shared helpers for the read-only journal, scores and notebook requests (ATL-200).
 */
abstract class JournalApiRequest extends FormRequest
{
    public function authorize(): bool
    {
        return $this->user() !== null;
    }

    protected static function int(string $key, int $default): int
    {
        $value = config('atlas_defaults.journal_api.'.$key);

        return is_int($value) ? $value : $default;
    }

    public static function timezone(): string
    {
        $tz = config('atlas_defaults.timezone.display');

        return is_string($tz) ? $tz : 'Asia/Baghdad';
    }

    protected static function text(mixed $value): ?string
    {
        return is_string($value) && $value !== '' ? $value : null;
    }
}
