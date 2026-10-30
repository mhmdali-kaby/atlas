<?php

declare(strict_types=1);

namespace App\Support\Portfolio;

use Carbon\CarbonImmutable;

/**
 * Validated filters of a Portfolio History query (ATL-172). Dates are already the UTC bounds of the
 * owner's Asia/Baghdad days: [fromUtc, toUtc).
 */
final readonly class HistoryFilters
{
    public function __construct(
        public string $type,
        public string $env,
        public ?string $instId = null,
        public ?string $assetClass = null,
        public ?string $agent = null,
        public ?string $strategyTag = null,
        public ?CarbonImmutable $fromUtc = null,
        public ?CarbonImmutable $toUtc = null,
        public ?string $side = null,
        public ?string $winLoss = null,
    ) {}

    /**
     * For the audit_log row of an export.
     *
     * @return array<string, string>
     */
    public function toArray(): array
    {
        return array_filter([
            'type' => $this->type,
            'env' => $this->env,
            'inst_id' => $this->instId,
            'asset_class' => $this->assetClass,
            'agent' => $this->agent,
            'strategy_tag' => $this->strategyTag,
            'from_utc' => $this->fromUtc?->toIso8601ZuluString(),
            'to_utc' => $this->toUtc?->toIso8601ZuluString(),
            'side' => $this->side,
            'win_loss' => $this->winLoss,
        ], static fn (?string $v): bool => $v !== null);
    }
}
