import {
    ArrowDownRight,
    ArrowUpRight,
    CalendarCheck,
    Activity,
    Minus,
    type LucideIcon,
} from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { formatSigned, signOf, type Lang } from "../../../lib/format";
import { sgnClass } from "./pm";
import {
    formatFullTime,
    formatTime,
    formatUtcTime,
    baghdadDay,
    formatDateTime,
} from "../time";
import { AsOf } from "./TechnicalSetupCard";

/* Shared pieces of the Portfolio Manager's cards (ATL-178, design v14 wr-analysts.js). */

export function Fig({
    children,
    className = "",
}: {
    children: ReactNode;
    className?: string;
}) {
    return (
        <bdi dir="ltr" className={`fig num ${className}`.trim()}>
            {children}
        </bdi>
    );
}

/** A signed USD figure: colour, a sign and an icon together, so colour is never alone. */
export function Signed({
    value,
    lang,
    big = false,
    testId,
}: {
    value: string | null;
    lang: Lang;
    big?: boolean;
    testId?: string;
}) {
    const s = signOf(value);
    const Icon = s === 1 ? ArrowUpRight : s === -1 ? ArrowDownRight : Minus;
    return (
        <span
            className={`an-sig ${sgnClass(value)} ${big ? "is-big" : ""}`.trim()}
            data-testid={testId}
        >
            <Icon className="icon" aria-hidden="true" />
            <Fig>{formatSigned(value, "USD", lang)}</Fig>
        </span>
    );
}

/** A 0..1 bar; decorative (the figure beside it carries the value). */
export function Meter({
    value,
    warn = false,
    wide = false,
}: {
    value: number;
    warn?: boolean;
    wide?: boolean;
}) {
    const v = Math.max(0, Math.min(1, value));
    return (
        <span
            className={`an-meter ${wide ? "is-wide" : ""} ${warn ? "is-warn" : ""}`.trim()}
            aria-hidden="true"
        >
            <i style={{ "--v": v.toFixed(3) } as CSSProperties} />
        </span>
    );
}

/** A small time for a list row, Baghdad (with the date when not today); both zones in the tooltip. */
export function Stamp({
    ts,
    lang,
    now,
}: {
    ts: string;
    lang: Lang;
    now: number;
}) {
    const { t } = useTranslation("warroom");
    const text =
        baghdadDay(ts) === baghdadDay(now)
            ? formatTime(ts, lang)
            : formatDateTime(ts, lang);
    return (
        <time
            className="an-stamp num"
            dateTime={ts}
            tabIndex={0}
            title={t("ta.dual", {
                baghdad: formatFullTime(ts, lang),
                utc: formatUtcTime(ts),
            })}
        >
            {text}
        </time>
    );
}

const HEAD_ICON: Record<"pu" | "daily", LucideIcon> = {
    pu: Activity,
    daily: CalendarCheck,
};

export function CardHead({
    kind,
    asOf,
    lang,
}: {
    kind: "pu" | "daily";
    asOf: string;
    lang: Lang;
}) {
    const { t } = useTranslation("warroom");
    const Icon = HEAD_ICON[kind];
    return (
        <div className="hv-head">
            <span className="hv-tile">
                <Icon aria-hidden="true" strokeWidth={1.5} />
            </span>
            <span className="hv-kind">
                {t(kind === "pu" ? "pm.kindPu" : "pm.kindDaily")}
            </span>
            <AsOf ts={asOf} lang={lang} />
        </div>
    );
}
