<?php

declare(strict_types=1);

namespace App\Support\Proposals;

use App\Models\Proposal;
use Brick\Math\BigDecimal;
use Brick\Math\RoundingMode;

/**
 * ATL-162: the owner-edit rules for an entry proposal (brief §7.6 "never move a stop further away",
 * §7.8 "never widen a stop", rule 3 "every order has a stop").
 *
 * - The stop may only tighten: a buy's stop may rise (never fall), a sell's may fall (never rise),
 *   and it stays on the loss side of the entry (entry, else the entry-zone midpoint).
 * - Targets stay on the profit side of the entry (of the stop for a market order with no entry),
 *   1-3 of them, % adding up to at most 100 — the same as atlas_shared.proposals.TradeProposal.
 */
final class ProposalRules
{
    /** Statuses the owner may reject. Anything later is already sent or closed. */
    public const REJECTABLE = ['awaiting_owner', 'advise_only'];

    public static function reference(Proposal $p): ?BigDecimal
    {
        $entry = Dec::of($p->entry);
        if ($entry !== null) {
            return $entry;
        }
        $zone = $p->entry_zone;
        if (is_array($zone) && count($zone) === 2) {
            $low = Dec::of($zone[0] ?? null);
            $high = Dec::of($zone[1] ?? null);
            if ($low !== null && $high !== null) {
                return $low->plus($high)->dividedBy(2, 18, RoundingMode::HalfUp);
            }
        }

        return null;
    }

    /** null when the new stop is acceptable, else a translation key under proposals.*. */
    public static function stopError(Proposal $p, BigDecimal $stop): ?string
    {
        $old = Dec::of($p->stop);
        $ref = self::reference($p);
        $buy = $p->side === 'buy';
        if (! $stop->isPositive()) {
            return 'stop_not_positive';
        }
        if ($old !== null && ($buy ? $stop->isLessThan($old) : $stop->isGreaterThan($old))) {
            return 'stop_widened';
        }
        if ($ref !== null && ($buy ? $stop->isGreaterThanOrEqualTo($ref) : $stop->isLessThanOrEqualTo($ref))) {
            return 'stop_wrong_side';
        }

        return null;
    }

    /**
     * @param  list<array{price: BigDecimal, pct: BigDecimal}>  $targets
     */
    public static function targetsError(Proposal $p, array $targets, BigDecimal $stop): ?string
    {
        $ref = self::reference($p) ?? $stop;
        $buy = $p->side === 'buy';
        $total = BigDecimal::zero();
        foreach ($targets as $tp) {
            if ($buy ? $tp['price']->isLessThanOrEqualTo($ref) : $tp['price']->isGreaterThanOrEqualTo($ref)) {
                return 'target_wrong_side';
            }
            $total = $total->plus($tp['pct']);
        }
        if ($total->isGreaterThan(100)) {
            return 'targets_over_100';
        }

        return null;
    }
}
