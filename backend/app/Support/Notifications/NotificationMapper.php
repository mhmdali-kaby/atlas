<?php

declare(strict_types=1);

namespace App\Support\Notifications;

use App\Models\WarroomMessage;
use App\Support\Events\BridgeEventHandler;
use Brick\Math\BigDecimal;
use Illuminate\Support\Str;

/**
 * Which bus envelopes are brief §17 events, and what their notification says (ATL-214).
 *
 * | event                       | stream          | envelope                                                          |
 * |-----------------------------|-----------------|-------------------------------------------------------------------|
 * | proposal_awaiting_approval  | atlas.proposals | proposal.awaiting_owner                                           |
 * | order_filled                | atlas.orders    | order.state_changed, to_state filled, origin atlas                |
 * | stop_hit / target_hit       | atlas.orders    | algo_order.state_changed to effective; kind sl/tp, oco by actual_side (else nearest trigger) |
 * | circuit_breaker             | atlas.system    | system.circuit_breaker tripped (any name but the two below)       |
 * | data_stale                  | atlas.system    | system.circuit_breaker tripped, name data_stale                   |
 * | reconciliation_mismatch     | atlas.system    | system.circuit_breaker tripped, name reconciliation_mismatch      |
 * | kill_switch                 | atlas.system    | system.kill                                                       |
 * | agent_degraded              | atlas.system    | system.agent_state into degraded or rate_limited                  |
 * | daily_report                | atlas.warroom   | warroom.message from the PM with card.type daily_report           |
 * | news_alert                  | atlas.warroom   | news.alert on a held instrument (reason held_instrument)          |
 *
 * params are an allow-list of scalar fields per event, cut to notifications.param_max_length:
 * nothing else in a payload (a connection key, secret, UID, an error text) can reach a row.
 */
final class NotificationMapper
{
    public const TITLE_KEY = 'notifications.events.%s.title';

    /**
     * @param  array{id: string, type: string, ts: string, env: string, source: string, correlation_id: string|null, payload: array<string, mixed>}  $envelope
     * @param  WarroomMessage|null  $message  the stored War Room message of a warroom.message/news.alert envelope
     * @return array{event: string, severity: 'info'|'warning'|'critical', params: array<string, bool|int|float|string|list<string>|null>, link: string|null}|null
     */
    public function map(string $stream, array $envelope, ?WarroomMessage $message = null): ?array
    {
        $p = $envelope['payload'];

        return match (true) {
            $stream === 'atlas.proposals' && $envelope['type'] === 'proposal.awaiting_owner' => $this->awaitingApproval($p),
            $stream === 'atlas.orders' && $envelope['type'] === 'order.state_changed' => $this->orderFilled($p),
            $stream === 'atlas.orders' && $envelope['type'] === 'algo_order.state_changed' => $this->algoTriggered($p),
            $stream === 'atlas.system' && $envelope['type'] === 'system.circuit_breaker' => $this->breaker($p),
            $stream === 'atlas.system' && $envelope['type'] === 'system.kill' => $this->kill($p),
            $stream === 'atlas.system' && $envelope['type'] === 'system.agent_state' => $this->agentState($p),
            $stream === 'atlas.warroom' && $envelope['type'] === BridgeEventHandler::MESSAGE_EVENT => $this->dailyReport($message),
            $stream === 'atlas.warroom' && $envelope['type'] === BridgeEventHandler::NEWS_ALERT_EVENT => $this->newsAlert($message),
            default => null,
        };
    }

    public static function titleKey(string $event): string
    {
        return sprintf(self::TITLE_KEY, $event);
    }

    /**
     * @param  array<string, mixed>  $p
     * @return array{event: string, severity: 'info'|'warning'|'critical', params: array<string, bool|int|float|string|list<string>|null>, link: string|null}|null
     */
    private function awaitingApproval(array $p): ?array
    {
        $id = self::uuid($p['proposal_id'] ?? null);
        if ($id === null) {
            return null;
        }

        return self::draft('proposal_awaiting_approval', 'warning', [
            'proposal_id' => $id,
            'inst_id' => self::str($p['inst_id'] ?? null),
            'side' => self::str($p['side'] ?? null),
            'kind' => self::str($p['kind'] ?? null),
            'final_notional_usd' => self::decimal($p['final_notional_usd'] ?? null),
            'approval_expires_at' => self::str($p['approval_expires_at'] ?? null),
        ], '/warroom?proposal='.$id);
    }

    /**
     * @param  array<string, mixed>  $p
     * @return array{event: string, severity: 'info'|'warning'|'critical', params: array<string, bool|int|float|string|list<string>|null>, link: string|null}|null
     */
    private function orderFilled(array $p): ?array
    {
        // Only Atlas's own orders: an OKX stop/target firing arrives as an external order and is
        // told as stop_hit/target_hit instead.
        if (($p['to_state'] ?? null) !== 'filled' || ($p['from_state'] ?? null) === 'filled' || ($p['origin'] ?? null) !== 'atlas') {
            return null;
        }
        $inst = self::str($p['inst_id'] ?? null);

        return self::draft('order_filled', 'info', [
            'order_id' => self::uuid($p['order_id'] ?? null),
            'proposal_id' => self::uuid($p['proposal_id'] ?? null),
            'inst_id' => $inst,
            'side' => self::str($p['side'] ?? null),
            'purpose' => self::str($p['purpose'] ?? null),
            'filled_sz' => self::decimal($p['filled_sz'] ?? null),
            'avg_px' => self::decimal($p['avg_px'] ?? null),
        ], '/portfolio/orders'.($inst !== null ? '?inst_id='.rawurlencode($inst) : ''));
    }

    /**
     * @param  array<string, mixed>  $p
     * @return array{event: string, severity: 'info'|'warning'|'critical', params: array<string, bool|int|float|string|list<string>|null>, link: string|null}|null
     */
    private function algoTriggered(array $p): ?array
    {
        if (($p['to_state'] ?? null) !== 'effective' || ($p['from_state'] ?? null) === 'effective') {
            return null;
        }
        $leg = match ($p['kind'] ?? null) {
            'sl' => 'sl',
            'tp' => 'tp',
            'oco' => self::ocoLeg($p),
            default => null,
        };
        if ($leg === null) {
            return null;
        }
        $inst = self::str($p['inst_id'] ?? null);
        $trigger = $leg === 'sl'
            ? self::decimal($p['sl_trigger_px'] ?? $p['trigger_px'] ?? null)
            : self::decimal($p['tp_trigger_px'] ?? $p['trigger_px'] ?? null);

        return self::draft($leg === 'sl' ? 'stop_hit' : 'target_hit', $leg === 'sl' ? 'warning' : 'info', [
            'algo_order_id' => self::uuid($p['algo_order_id'] ?? null),
            'inst_id' => $inst,
            'side' => self::str($p['side'] ?? null),
            'trigger_px' => $trigger,
            'actual_px' => self::decimal($p['actual_px'] ?? null),
            'sz' => self::decimal($p['sz'] ?? null),
        ], '/portfolio/positions'.($inst !== null ? '?inst_id='.rawurlencode($inst) : ''));
    }

    /**
     * Which leg of an OCO fired: OKX's actualSide, else the trigger nearest the fill price, else
     * unknown (no notification rather than a wrong one).
     *
     * @param  array<string, mixed>  $p
     */
    private static function ocoLeg(array $p): ?string
    {
        $side = $p['actual_side'] ?? null;
        if ($side === 'sl' || $side === 'tp') {
            return $side;
        }
        $px = self::decimal($p['actual_px'] ?? null);
        $sl = self::decimal($p['sl_trigger_px'] ?? null);
        $tp = self::decimal($p['tp_trigger_px'] ?? null);
        if ($px === null || $sl === null || $tp === null) {
            return null;
        }
        $toSl = BigDecimal::of($px)->minus($sl)->abs();
        $toTp = BigDecimal::of($px)->minus($tp)->abs();

        return $toSl->isLessThanOrEqualTo($toTp) ? 'sl' : 'tp';
    }

    /**
     * @param  array<string, mixed>  $p
     * @return array{event: string, severity: 'info'|'warning'|'critical', params: array<string, bool|int|float|string|list<string>|null>, link: string|null}|null
     */
    private function breaker(array $p): ?array
    {
        if (($p['state'] ?? null) !== 'tripped') {
            return null;
        }
        $name = self::str($p['name'] ?? null);
        if ($name === null) {
            return null;
        }
        $params = [
            'name' => $name,
            'reason' => self::str($p['reason'] ?? null),
            'since' => self::str($p['since'] ?? null),
            'self_clearing' => is_bool($p['self_clearing'] ?? null) ? $p['self_clearing'] : null,
        ];

        return match ($name) {
            'data_stale' => self::draft('data_stale', 'warning', $params, '/settings/data'),
            'reconciliation_mismatch' => self::draft('reconciliation_mismatch', 'critical', $params, '/portfolio/positions'),
            default => self::draft('circuit_breaker', 'critical', $params, '/warroom'),
        };
    }

    /**
     * @param  array<string, mixed>  $p
     * @return array{event: string, severity: 'info'|'warning'|'critical', params: array<string, bool|int|float|string|list<string>|null>, link: string|null}
     */
    private function kill(array $p): array
    {
        return self::draft('kill_switch', 'critical', [
            'flatten' => ($p['flatten'] ?? false) === true,
            'ok' => is_bool($p['ok'] ?? null) ? $p['ok'] : null,
            'cancelled' => self::int($p['cancelled'] ?? null),
            'closed' => self::int($p['closed'] ?? null),
            'stops_kept' => self::int($p['stops_kept'] ?? null),
            'failed' => is_array($p['failed'] ?? null) ? count($p['failed']) : null,
        ], '/warroom');
    }

    /**
     * @param  array<string, mixed>  $p
     * @return array{event: string, severity: 'info'|'warning'|'critical', params: array<string, bool|int|float|string|list<string>|null>, link: string|null}|null
     */
    private function agentState(array $p): ?array
    {
        $states = config('atlas_defaults.notifications.degraded_agent_states');
        $states = is_array($states) ? $states : ['degraded', 'rate_limited'];
        $state = $p['state'] ?? null;
        if (! in_array($state, $states, true) || in_array($p['previous_state'] ?? null, $states, true)) {
            return null;
        }
        $agent = self::str($p['agent'] ?? null);
        if ($agent === null || preg_match('/^[a-z0-9_]+$/', $agent) !== 1) {
            return null;
        }

        return self::draft('agent_degraded', 'warning', [
            'agent' => $agent,
            'state' => self::str($state),
            'previous_state' => self::str($p['previous_state'] ?? null),
            'resets_at' => self::str($p['resets_at'] ?? null),
        ], '/agents');
    }

    /**
     * @return array{event: string, severity: 'info'|'warning'|'critical', params: array<string, bool|int|float|string|list<string>|null>, link: string|null}|null
     */
    private function dailyReport(?WarroomMessage $message): ?array
    {
        if ($message === null || $message->author_type !== 'agent' || $message->author !== 'portfolio_manager') {
            return null;
        }
        $card = $message->card ?? [];
        if (($card['type'] ?? null) !== 'daily_report') {
            return null;
        }

        return self::draft('daily_report', 'info', [
            'message_id' => $message->id,
            'day' => self::str($card['day'] ?? $card['date'] ?? null),
        ], '/warroom?message='.$message->id);
    }

    /**
     * @return array{event: string, severity: 'info'|'warning'|'critical', params: array<string, bool|int|float|string|list<string>|null>, link: string|null}|null
     */
    private function newsAlert(?WarroomMessage $message): ?array
    {
        if ($message === null || $message->kind !== 'news_alert') {
            return null;
        }
        $card = $message->card ?? [];
        $held = array_values(array_filter(is_array($card['held_inst_ids'] ?? null) ? $card['held_inst_ids'] : [], 'is_string'));
        if (($card['reason'] ?? null) !== 'held_instrument' && $held === []) {
            return null;
        }
        $importance = self::int($card['importance'] ?? null);

        return self::draft('news_alert', $importance !== null && $importance >= 5 ? 'critical' : 'warning', [
            'message_id' => $message->id,
            // The headline is untrusted source text: plain text only, as content_md already is.
            'headline' => self::str($message->content_md),
            'source_name' => self::str($card['source_name'] ?? null),
            'importance' => $importance,
            'inst_ids' => array_slice(array_map(fn (string $id): string => (string) self::str($id), $held), 0, 20),
        ], '/warroom?message='.$message->id);
    }

    /**
     * @param  array<string, bool|int|float|string|list<string>|null>  $params
     * @param  'info'|'warning'|'critical'  $severity
     * @return array{event: string, severity: 'info'|'warning'|'critical', params: array<string, bool|int|float|string|list<string>|null>, link: string|null}
     */
    private static function draft(string $event, string $severity, array $params, ?string $link): array
    {
        return ['event' => $event, 'severity' => $severity, 'params' => $params, 'link' => $link];
    }

    private static function str(mixed $value): ?string
    {
        if (! is_string($value) || trim($value) === '') {
            return null;
        }
        $max = config('atlas_defaults.notifications.param_max_length');

        return mb_substr(BridgeEventHandler::plainContent($value), 0, is_int($max) ? $max : 300);
    }

    private static function uuid(mixed $value): ?string
    {
        return is_string($value) && Str::isUuid($value) ? strtolower($value) : null;
    }

    private static function decimal(mixed $value): ?string
    {
        if (is_int($value)) {
            return (string) $value;
        }

        return is_string($value) && preg_match('/^-?\d{1,30}(\.\d{1,18})?$/', $value) === 1 ? $value : null;
    }

    private static function int(mixed $value): ?int
    {
        return is_int($value) ? $value : null;
    }
}
