import { signOf } from "../../../lib/format";

/*
 * The Portfolio Manager's two cards (brief §7.8, ATL-178):
 *
 * - `daily_report`: the `card` of a research_report message with `type: "daily_report"`, as written
 *   by services/agents/src/atlas_agents/pm/daily_report.py (ATL-170). Every figure is code's.
 * - `position_update`: the `card` of a position_update message with `type: "position_update"`, as
 *   written by atlas_agents/pm/review.py::status_card (ATL-178).
 *
 * Money, prices and R stay decimal strings so nothing a figure shows passes through a float. A reader
 * returns null when the card lacks what its renderer needs, and the GenericCard draws the message.
 */

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj =>
    typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null =>
    typeof v === "string" && v.trim() !== ""
        ? v
        : typeof v === "number" && Number.isFinite(v)
          ? String(v)
          : null;
const int = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v)
        ? Math.trunc(v)
        : typeof v === "string" && /^-?\d+$/.test(v.trim())
          ? Number(v)
          : null;
const side = (v: unknown): "long" | "short" | null =>
    v === "long" || v === "short" ? v : null;
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** Tick precision for a set of price strings: the most decimals any of them carries (max 8). */
export function decimalsOf(prices: readonly (string | null)[]): number {
    let d = 0;
    for (const p of prices) {
        if (!p) continue;
        const m = /\.(\d+)$/.exec(p.trim());
        if (m) d = Math.max(d, m[1]!.replace(/0+$/, "").length);
    }
    return Math.min(8, Math.max(d, 1));
}

/* ------------------------------------------------------------------ daily_report */

export interface ClosedTrade {
    instId: string;
    side: "long" | "short" | null;
    r: string | null;
    pnlUsd: string | null;
    holdingMinutes: number | null;
    closingRule: string | null;
    proposalId: string | null;
}

export interface BestWorst {
    instId: string;
    r: string | null;
    pnlUsd: string | null;
    note: string | null;
}

export interface LimitUsage {
    key: "daily_loss" | "open_positions";
    used: string;
    cap: string;
    unit: "pct" | "count";
}

export interface Breach {
    limit: string;
    used: string | null;
    cap: string | null;
    at: string | null;
}

export interface CarriedPosition {
    instId: string;
    side: "long" | "short" | null;
}

export interface ReportEvent {
    name: string;
    country: string | null;
    importance: number | null;
    at: string;
}

export interface DailyReport {
    day: string;
    env: string | null;
    asOf: string;
    equity: {
        start: string | null;
        end: string | null;
        change: string | null;
        changePct: string | null;
    };
    pnl: {
        realised: string | null;
        unrealised: string | null;
        fees: string | null;
        funding: string | null;
    };
    trades: ClosedTrade[];
    best: BestWorst | null;
    worst: BestWorst | null;
    limits: LimitUsage[];
    exposure: { usd: string | null; pct: string | null };
    breaches: Breach[];
    carried: CarriedPosition[];
    events: ReportEvent[];
}

function bestWorst(v: unknown): BestWorst | null {
    if (!isObj(v)) return null;
    const instId = str(v.inst_id);
    if (!instId) return null;
    return { instId, r: str(v.r), pnlUsd: str(v.pnl_usd), note: str(v.note) };
}

export function readDailyReport(card: unknown): DailyReport | null {
    if (!isObj(card) || card.type !== "daily_report") return null;
    const day = str(card.day);
    const asOf = str(card.as_of);
    if (!day || !asOf || !isObj(card.equity) || !isObj(card.pnl)) return null;
    const eq = card.equity;
    const pnl = card.pnl;
    const lim = isObj(card.limits) ? card.limits : {};
    const tomorrow = isObj(card.tomorrow) ? card.tomorrow : {};

    const trades: ClosedTrade[] = [];
    for (const x of list(card.trades)) {
        if (!isObj(x)) continue;
        const instId = str(x.inst_id);
        if (!instId) continue;
        trades.push({
            instId,
            side: side(x.side),
            r: str(x.r),
            pnlUsd: str(x.pnl_usd),
            holdingMinutes: int(x.holding_minutes),
            closingRule: str(x.closing_rule),
            proposalId: str(x.proposal_id),
        });
    }

    const limits: LimitUsage[] = [];
    const lossUsed = str(lim.peak_daily_loss_pct);
    const lossCap = str(lim.max_daily_loss_pct);
    if (lossUsed && lossCap && Number(lossCap) > 0)
        limits.push({
            key: "daily_loss",
            used: lossUsed,
            cap: lossCap,
            unit: "pct",
        });
    const posUsed = str(lim.peak_open_positions);
    const posCap = str(lim.max_open_positions);
    if (posUsed && posCap && Number(posCap) > 0)
        limits.push({
            key: "open_positions",
            used: posUsed,
            cap: posCap,
            unit: "count",
        });

    const breaches: Breach[] = [];
    for (const b of list(card.breaches)) {
        if (!isObj(b)) continue;
        const limit = str(b.limit);
        if (!limit) continue;
        breaches.push({
            limit,
            used: str(b.used),
            cap: str(b.cap),
            at: str(b.at),
        });
    }

    const carried: CarriedPosition[] = [];
    for (const p of list(tomorrow.positions)) {
        if (!isObj(p)) continue;
        const instId = str(p.inst_id);
        if (instId) carried.push({ instId, side: side(p.side) });
    }
    const events: ReportEvent[] = [];
    for (const e of list(tomorrow.events)) {
        if (!isObj(e)) continue;
        const name = str(e.name);
        const at = str(e.scheduled_at);
        if (name && at)
            events.push({
                name,
                country: str(e.country),
                importance: int(e.importance),
                at,
            });
    }

    return {
        day,
        env: str(card.env),
        asOf,
        equity: {
            start: str(eq.start_usd),
            end: str(eq.end_usd),
            change: str(eq.change_usd),
            changePct: str(eq.change_pct),
        },
        pnl: {
            realised: str(pnl.realised_usd),
            unrealised: str(pnl.unrealised_usd),
            fees: str(pnl.fees_usd),
            funding: str(pnl.funding_usd),
        },
        trades,
        best: bestWorst(card.best_trade),
        worst: bestWorst(card.worst_trade),
        limits,
        exposure: {
            usd: str(lim.peak_exposure_usd),
            pct: str(lim.peak_exposure_pct),
        },
        breaches,
        carried,
        events,
    };
}

/** A limit's usage as a 0..1 fraction (for the bar), from decimal strings. */
export function usageOf(l: LimitUsage): number {
    const v = Number(l.used) / Number(l.cap);
    return Number.isFinite(v) ? Math.max(0, v) : 0;
}

/* ------------------------------------------------------------------ position_update */

export interface PmAction {
    kind: "amend_stop" | "amend_targets" | "partial_close" | "close" | string;
    rule: string;
    stop: string | null;
    closePct: string | null;
    proposalId: string | null;
    status: string | null;
}

export interface PositionUpdate {
    instId: string;
    side: "long" | "short";
    entry: string;
    mark: string;
    stop: string | null;
    initialStop: string | null;
    targets: string[];
    tick: number;
    pnlUsd: string | null;
    r: string | null;
    stopAtr: string | null;
    tp1Atr: string | null;
    minutesInTrade: number | null;
    fundingPaidUsd: string | null;
    actions: PmAction[];
    advisories: { rule: string; reason: string }[];
    asOf: string;
}

export function readPositionUpdate(card: unknown): PositionUpdate | null {
    if (!isObj(card) || card.type !== "position_update") return null;
    const instId = str(card.inst_id);
    const s = side(card.side);
    const entry = str(card.entry);
    const mark = str(card.mark);
    const asOf = str(card.as_of);
    if (!instId || !s || !entry || !mark || !asOf) return null;
    const targets = list(card.targets)
        .map(str)
        .filter((x): x is string => x !== null);
    const actions: PmAction[] = [];
    for (const a of list(card.actions)) {
        if (!isObj(a)) continue;
        const kind = str(a.kind);
        if (!kind) continue;
        actions.push({
            kind,
            rule: str(a.rule) ?? "",
            stop: str(a.stop),
            closePct: str(a.close_pct),
            proposalId: str(a.proposal_id),
            status: str(a.status),
        });
    }
    const advisories: { rule: string; reason: string }[] = [];
    for (const a of list(card.advisories)) {
        if (!isObj(a)) continue;
        const rule = str(a.rule);
        if (rule) advisories.push({ rule, reason: str(a.reason) ?? "" });
    }
    const stop = str(card.stop);
    const initialStop = str(card.initial_stop);
    return {
        instId,
        side: s,
        entry,
        mark,
        stop,
        initialStop,
        targets,
        tick: decimalsOf([entry, mark, stop, initialStop, ...targets]),
        pnlUsd: str(card.pnl_usd),
        r: str(card.r),
        stopAtr: str(card.stop_distance_atr),
        tp1Atr: str(card.tp1_distance_atr),
        minutesInTrade: int(card.minutes_in_trade),
        fundingPaidUsd: str(card.funding_paid_usd),
        actions,
        advisories,
        asOf,
    };
}

/**
 * The i18n key of a rule label (ATL-169 `invalidation` / ATL-170 `closing_rule`), or null when the
 * label is not one Atlas knows and the raw text is shown. `event_reduce:<id>` is the event rule.
 */
export function ruleKey(label: string): string | null {
    const base = label.startsWith("event_reduce") ? "event_reduce" : label;
    return RULES.has(base) ? base : null;
}

const RULES = new Set([
    "breakeven",
    "tp1_partial",
    "tp1_trail",
    "two_r_trail",
    "time_stop",
    "invalidation",
    "event_reduce",
    "funding_cost",
    "close",
    "partial_close",
    "stop_loss",
    "take_profit",
    "liquidation",
    "adl",
    "manual",
]);

/** The CSS sign class of a decimal string: is-pos, is-neg or is-zero. */
export function sgnClass(v: string | null): string {
    const s = signOf(v);
    return s === 1 ? "is-pos" : s === -1 ? "is-neg" : "is-zero";
}

/** A rule label in words (pm.rules.*), or the raw label when Atlas does not know it. */
export function ruleText(t: (key: string) => string, label: string): string {
    const key = ruleKey(label);
    return key ? t(`pm.rules.${key}`) : label;
}
