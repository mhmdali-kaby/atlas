<?php

declare(strict_types=1);

namespace App\Http\Requests\News;

/**
 * GET /api/sentiment/latest?index=crypto_fng
 */
class LatestSentimentRequest extends NewsApiRequest
{
    /**
     * @return array<string, list<mixed>>
     */
    public function rules(): array
    {
        return [
            'index' => ['sometimes', 'string', 'max:64', 'regex:/^[a-z0-9_.]+$/'],
        ];
    }

    public function indexKey(): string
    {
        $value = $this->validated('index');

        return is_string($value) ? $value : self::stringDefault('default_sentiment_index', 'crypto_fng');
    }
}
