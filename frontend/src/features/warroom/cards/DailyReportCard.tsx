import {
    ArrowRight,
    CircleCheck,
    OctagonAlert,
    TrendingDown,
    TrendingUp,
    TriangleAlert,
    Trophy,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import {
    formatFixed,
    formatMoney,
    formatSigned,
    type Lang,
} from "../../../lib/format";
import { useNow } from "../../portfolio/hooks";
import { Duration } from "../../portfolio/parts";
import { fillSlots, slot } from "../slots";
import { CardHead, Fig, Meter, Signed, Stamp } from "./pmParts";
import {
    ruleText,
    sgnClass,
    usageOf,
    type BestWorst,
    type Breach,
    type DailyReport,
    type LimitUsage,
} from "./pm";
import { rText } from "./rText";
import { InstrumentButton } from "./TechnicalSetupCard";
import "./pm.css";

/*
 * The Portfolio Manager's daily report (brief §7.8 "DAILY REPORT", design v14 wr-analysts.js
 * daily_report): equity start → end with the change; realised, unrealised, fees and funding; the
 * closed trades; best and worst; limit usage bars; breaches in the alert style (an icon, a title and
 * a sentence, never colour alone); tomorrow's carried positions and events. Every figure is code's
 * (ATL-170); the model writes only the best/worst notes.
 */

const WARN_AT = 0.7;

/** A business day ("2026-10-27") as words. The day is a date, not an instant, so it is read in UTC. */
function dayText(day: string, lang: Lang): string {
    return new Intl.DateTimeFormat(
        lang === "en" ? "en-GB" : "ar-IQ-u-nu-latn",
        { timeZone: "UTC", weekday: "long", day: "numeric", month: "long" },
    ).format(new Date(`${day}T12:00:00Z`));
}

function pctText(v: string, lang: Lang, digits = 1): string {
    return `${formatFixed(v, digits, lang)}%`;
}

function LimitRow({ limit, lang }: { limit: LimitUsage; lang: Lang }) {
    const { t } = useTranslation("warroom");
    const v = usageOf(limit);
    const warn = v >= WARN_AT;
    const used =
        limit.unit === "pct"
            ? pctText(limit.used, lang)
            : formatFixed(limit.used, 0, lang);
    const cap =
        limit.unit === "pct"
            ? pctText(limit.cap, lang, Number(limit.cap) % 1 ? 1 : 0)
            : formatFixed(limit.cap, 0, lang);
    return (
        <li
            className={`an-lim ${warn ? "is-warn" : ""}`.trim()}
            data-testid="dr-limit"
            data-limit={limit.key}
        >
            <span className="an-lim-k">{t(`pm.l.${limit.key}`)}</span>
            <Meter value={v} warn={warn} wide />
            <span className="an-lim-v">
                {warn && <TriangleAlert className="icon" aria-hidden="true" />}
                <span>
                    {fillSlots(
                        t("pm.of", {
                            used: slot("used"),
                            limit: slot("limit"),
                        }),
                        { used: <Fig>{used}</Fig>, limit: <Fig>{cap}</Fig> },
                    )}
                </span>
                {warn && (
                    <Fig className="an-lim-pct">{`${formatFixed(String(Math.round(v * 100)), 0, lang)}%`}</Fig>
                )}
            </span>
        </li>
    );
}

function BreachBox({
    breaches,
    lang,
    now,
}: {
    breaches: Breach[];
    lang: Lang;
    now: number;
}) {
    const { t } = useTranslation("warroom");
    const known = [
        "max_daily_loss_pct",
        "max_weekly_loss_pct",
        "max_open_positions",
    ];
    const value = (b: Breach, v: string | null) =>
        v === null
            ? "—"
            : b.limit === "max_open_positions"
              ? formatFixed(v, 0, lang)
              : pctText(v, lang, Number(v) % 1 ? 2 : 0);
    return (
        <div className="an-breach" role="alert" data-testid="dr-breach">
            <OctagonAlert className="icon" aria-hidden="true" />
            <div>
                <p className="an-breach-t">
                    {t("pm.breachTitle", { count: breaches.length })}
                </p>
                {breaches.map((b, i) => (
                    <p
                        key={`${b.limit}-${i}`}
                        className="an-breach-b"
                        data-testid="dr-breach-row"
                        data-limit={b.limit}
                    >
                        {fillSlots(
                            t(
                                known.includes(b.limit)
                                    ? `pm.breach.${b.limit}`
                                    : "pm.breach.other",
                                {
                                    limit: b.limit,
                                    used: slot("used"),
                                    cap: slot("cap"),
                                },
                            ),
                            {
                                used: <Fig>{value(b, b.used)}</Fig>,
                                cap: <Fig>{value(b, b.cap)}</Fig>,
                            },
                        )}
                        {b.at && (
                            <>
                                {" "}
                                {fillSlots(
                                    t("pm.breachAt", { time: slot("time") }),
                                    {
                                        time: (
                                            <Stamp
                                                ts={b.at}
                                                lang={lang}
                                                now={now}
                                            />
                                        ),
                                    },
                                )}
                            </>
                        )}
                    </p>
                ))}
            </div>
        </div>
    );
}

function BestWorstRow({
    kind,
    trade,
}: {
    kind: "best" | "worst";
    trade: BestWorst;
}) {
    const { t } = useTranslation("warroom");
    const Icon = kind === "best" ? Trophy : TrendingDown;
    return (
        <li className={`an-bw is-${kind}`} data-testid={`dr-${kind}`}>
            <Icon className="icon" aria-hidden="true" />
            <span className="an-bw-k">{t(`pm.${kind}`)}</span>
            <InstrumentButton instId={trade.instId} />
            {trade.r !== null && (
                <Fig className={`an-r ${sgnClass(trade.r)}`}>
                    {rText(trade.r)}
                </Fig>
            )}
            {trade.note && (
                <span className="an-bw-why" dir="auto">
                    {trade.note}
                </span>
            )}
        </li>
    );
}

export function DailyReportCard({
    report,
    lang,
}: {
    report: DailyReport;
    lang: Lang;
}) {
    const { t } = useTranslation("warroom");
    const now = useNow(60_000);
    const eq = report.equity;
    const parts: [string, string | null][] = [
        ["pm.realised", report.pnl.realised],
        ["pm.unrealised", report.pnl.unrealised],
        ["pm.fees", report.pnl.fees],
        ["pm.funding", report.pnl.funding],
    ];
    return (
        <div
            className="hv-card an-card an-daily"
            role="group"
            aria-label={t("pm.kindDaily")}
            data-testid="dr-card"
        >
            <CardHead kind="daily" asOf={report.asOf} lang={lang} />
            <p className="an-day">
                {t("pm.dayOf", { date: dayText(report.day, lang) })}
            </p>
            <div className="an-eq" data-testid="dr-equity">
                <p className="an-eq-k">{t("pm.equity")}</p>
                <p className="an-eq-v">
                    <Fig>{formatMoney(eq.start, "USD", lang)}</Fig>
                    <ArrowRight
                        className="icon icon-dir an-eq-arrow"
                        aria-hidden="true"
                    />
                    <Fig className="an-eq-end">
                        {formatMoney(eq.end, "USD", lang)}
                    </Fig>
                </p>
                <p className="an-eq-ch">
                    <Signed
                        value={eq.change}
                        lang={lang}
                        big
                        testId="dr-change"
                    />
                    {eq.changePct !== null && (
                        <Fig className={`an-r ${sgnClass(eq.changePct)}`}>
                            {formatSigned(eq.changePct, "percent", lang)}
                        </Fig>
                    )}
                </p>
            </div>
            <dl className="an-parts">
                {parts.map(([k, v]) => (
                    <div key={k} className="an-part">
                        <dt>{t(k)}</dt>
                        <dd>
                            <Signed value={v} lang={lang} />
                        </dd>
                    </div>
                ))}
            </dl>

            <section className="an-sec" aria-label={t("pm.closed")}>
                <h2 className="an-sec-h">
                    {t("pm.closed")}
                    <span className="an-count">
                        <Fig>
                            {formatFixed(String(report.trades.length), 0, lang)}
                        </Fig>
                    </span>
                </h2>
                {report.trades.length === 0 ? (
                    <p className="an-ok">{t("pm.noTrades")}</p>
                ) : (
                    <table className="an-table" data-testid="dr-trades">
                        <thead>
                            <tr>
                                <th scope="col">{t("pm.th.inst")}</th>
                                <th scope="col">{t("pm.th.side")}</th>
                                <th scope="col" className="an-n">
                                    {t("pm.th.r")}
                                </th>
                                <th scope="col" className="an-n">
                                    {t("pm.th.usd")}
                                </th>
                                <th scope="col" className="an-n an-held">
                                    {t("pm.th.held")}
                                </th>
                                <th scope="col">{t("pm.th.by")}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {report.trades.map((x, i) => (
                                <tr
                                    key={`${x.instId}-${i}`}
                                    data-testid="dr-trade"
                                >
                                    <th scope="row">
                                        <InstrumentButton instId={x.instId} />
                                    </th>
                                    <td className="an-side">
                                        {x.side ? t(`pm.${x.side}`) : "—"}
                                    </td>
                                    <td className="an-n">
                                        {x.r !== null ? (
                                            <Fig
                                                className={`an-r ${sgnClass(x.r)}`}
                                            >
                                                {rText(x.r)}
                                            </Fig>
                                        ) : (
                                            "—"
                                        )}
                                    </td>
                                    <td className="an-n">
                                        <Signed value={x.pnlUsd} lang={lang} />
                                    </td>
                                    <td className="an-n an-held">
                                        {x.holdingMinutes !== null ? (
                                            <Duration
                                                ms={x.holdingMinutes * 60_000}
                                                lang={lang}
                                            />
                                        ) : (
                                            "—"
                                        )}
                                    </td>
                                    <td className="an-by">
                                        {x.closingRule
                                            ? ruleText(t, x.closingRule)
                                            : "—"}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
                {(report.best || report.worst) && (
                    <ul className="an-bws">
                        {report.best && (
                            <BestWorstRow kind="best" trade={report.best} />
                        )}
                        {report.worst && (
                            <BestWorstRow kind="worst" trade={report.worst} />
                        )}
                    </ul>
                )}
            </section>

            <section className="an-sec" aria-label={t("pm.limits")}>
                <h2 className="an-sec-h">{t("pm.limits")}</h2>
                {(report.limits.length > 0 || report.exposure.usd !== null) && (
                    <ul className="an-lims">
                        {report.limits.map((l) => (
                            <LimitRow key={l.key} limit={l} lang={lang} />
                        ))}
                        {report.exposure.usd !== null && (
                            <li
                                className="an-lim is-plain"
                                data-testid="dr-limit"
                                data-limit="exposure_peak"
                            >
                                <span className="an-lim-k">
                                    {t("pm.l.exposure_peak")}
                                </span>
                                <span className="an-lim-v">
                                    {report.exposure.pct !== null ? (
                                        <span>
                                            {fillSlots(
                                                t("pm.exposureV", {
                                                    usd: slot("usd"),
                                                    pct: slot("pct"),
                                                }),
                                                {
                                                    usd: (
                                                        <Fig>
                                                            {formatMoney(
                                                                report.exposure
                                                                    .usd,
                                                                "USD",
                                                                lang,
                                                            )}
                                                        </Fig>
                                                    ),
                                                    pct: (
                                                        <Fig>
                                                            {pctText(
                                                                report.exposure
                                                                    .pct,
                                                                lang,
                                                            )}
                                                        </Fig>
                                                    ),
                                                },
                                            )}
                                        </span>
                                    ) : (
                                        <Fig>
                                            {formatMoney(
                                                report.exposure.usd,
                                                "USD",
                                                lang,
                                            )}
                                        </Fig>
                                    )}
                                </span>
                            </li>
                        )}
                    </ul>
                )}
                {report.breaches.length > 0 ? (
                    <BreachBox
                        breaches={report.breaches}
                        lang={lang}
                        now={now}
                    />
                ) : (
                    <p className="an-ok" data-testid="dr-no-breach">
                        <CircleCheck className="icon" aria-hidden="true" />
                        <span>{t("pm.noBreach")}</span>
                    </p>
                )}
            </section>

            <section className="an-sec" aria-label={t("pm.tomorrow")}>
                <h2 className="an-sec-h">{t("pm.tomorrow")}</h2>
                <div className="an-tmr">
                    <div>
                        <p className="an-tmr-k">{t("pm.carried")}</p>
                        {report.carried.length === 0 ? (
                            <p className="an-tmr-none">{t("pm.noCarried")}</p>
                        ) : (
                            <ul className="an-carries" data-testid="dr-carried">
                                {report.carried.map((p) => {
                                    const Icon =
                                        p.side === "short"
                                            ? TrendingDown
                                            : TrendingUp;
                                    return (
                                        <li key={p.instId} className="an-carry">
                                            <InstrumentButton
                                                instId={p.instId}
                                            />
                                            {p.side && (
                                                <span className="an-carry-side">
                                                    <Icon
                                                        className="icon"
                                                        aria-hidden="true"
                                                    />
                                                    {t(`pm.${p.side}`)}
                                                </span>
                                            )}
                                        </li>
                                    );
                                })}
                            </ul>
                        )}
                    </div>
                    <div>
                        <p className="an-tmr-k">{t("pm.events")}</p>
                        {report.events.length === 0 ? (
                            <p className="an-tmr-none">{t("pm.noEvents")}</p>
                        ) : (
                            <ul className="an-events" data-testid="dr-events">
                                {report.events.map((e, i) => (
                                    <li key={`${e.name}-${i}`}>
                                        <Stamp
                                            ts={e.at}
                                            lang={lang}
                                            now={now}
                                        />
                                        <span dir="auto">
                                            {e.country ? `${e.country} · ` : ""}
                                            {e.name}
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                </div>
            </section>
        </div>
    );
}
