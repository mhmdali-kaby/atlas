import { useQueryClient } from "@tanstack/react-query";
import {
    Bell,
    ChevronRight,
    CircleAlert,
    OctagonX,
    Route,
    Shield,
    ShieldCheck,
} from "lucide-react";
import { Fragment, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { ApiError } from "../../lib/api";
import { formatFixed, formatMoney } from "../../lib/format";
import { Button, Skeleton, toast } from "../../ui";
import { cx } from "../../ui/cx";
import {
    askApiValue,
    askInputText,
    AskField,
    AskLine,
    AskSentence,
    AutonomyPicker,
    enabledAutonomyLevels,
    MoneyFig,
    parseAsk,
    settingsKey,
    TurnOnSheet,
    useSettings,
    type SettingsChanges,
    type SettingsDoc,
    type SheetNotice,
} from "../onboarding";
import { useLang } from "../portfolio/hooks";
import { formatDayTime } from "../portfolio/time";
import "../settings-connections/settings.css";
import { useRiskPreview } from "../settings-money/model";
import {
    fieldErrors,
    historyKey,
    useSaveSettings,
    useSettingsHistory,
    type HistoryEntry,
} from "../settings-risk/model";
import "../settings-risk/risk.css";
import "./autonomy.css";

/*
 * Settings → Autonomy (brief §14.6, decision 254, ATL-216; design js/pages/settings-autonomy.js).
 * Leads with what is in force now: the sentence and the ask line. Raising autonomy (to 3 or 4,
 * or a higher amount at 3) opens the turn-on sheet, which saves; lowering saves at once.
 * There is no save bar on this pane.
 */

const ASK_FIELD = "autonomy_ask_above_usd";
/** config/defaults.yaml autonomy.approval_timeout_minutes; a settings value overrides it. */
const DEFAULT_APPROVAL_TIMEOUT_MINUTES = 15;

function fill(template: string, parts: Record<string, ReactNode>): ReactNode {
    return template.split(/(\{\{\w+\}\})/).map((piece, i) => {
        const name = /^\{\{(\w+)\}\}$/.exec(piece)?.[1];
        return <Fragment key={i}>{name ? parts[name] : piece}</Fragment>;
    });
}

const savedAsk = (doc: SettingsDoc): string =>
    String(doc.settings[ASK_FIELD] ?? "0");

function Section({
    id,
    title,
    note,
    children,
}: {
    id: string;
    title: string;
    note?: ReactNode;
    children: ReactNode;
}) {
    return (
        <section className="grid" aria-labelledby={id}>
            <h2
                id={id}
                className="px-4 pb-2 text-footnote font-semibold text-label-secondary"
            >
                {title}
            </h2>
            {children}
            {note && (
                <p className="px-4 pt-2 text-footnote text-label-secondary">
                    {note}
                </p>
            )}
        </section>
    );
}

function PaneHeader({ version }: { version: number | null }) {
    const { t } = useTranslation("settings-autonomy");
    const lang = useLang();
    return (
        <header className="st-pane st-risk-pane">
            <span className="st-pane-icon" aria-hidden="true">
                <Route strokeWidth={1.75} />
            </span>
            <div className="grid gap-0.5">
                <h2 className="text-title-2">{t("nav")}</h2>
                <p className="max-w-[54ch] text-subheadline text-label-secondary">
                    {t("intro")}
                </p>
            </div>
            {version !== null && (
                <span className="st-version" data-testid="settings-version">
                    {t("version", { v: formatFixed(version, 0, lang) })}
                </span>
            )}
        </header>
    );
}

function ChangeLine({ entry }: { entry: HistoryEntry }) {
    const { t } = useTranslation("settings-autonomy");
    const { t: tOb } = useTranslation("onboarding");
    const level = entry.diff.autonomy_level;
    const ask = entry.diff[ASK_FIELD];
    if (level && level.to !== null) {
        const name = tOb(`au.l${level.to}`);
        if (Number(level.to) === 3 && ask && ask.to !== null)
            return (
                <span>
                    {fill(t("h.levelAsk", { name }), {
                        amount: <MoneyFig value={ask.to} />,
                    })}
                </span>
            );
        return <span>{t("h.level", { name })}</span>;
    }
    if (ask)
        return (
            <span>
                {fill(t("h.ask"), {
                    from: <MoneyFig value={ask.from} />,
                    to: <MoneyFig value={ask.to} />,
                })}
            </span>
        );
    return null;
}

function Changes({
    entries,
    loading,
}: {
    entries: HistoryEntry[] | undefined;
    loading: boolean;
}) {
    const { t } = useTranslation("settings-autonomy");
    const lang = useLang();
    if (loading && !entries) {
        return (
            <div className="st-group" aria-busy="true">
                {[0, 1].map((i) => (
                    <div key={i} className="st-activity-row">
                        <Skeleton className="h-3.5 w-[30%]" />
                        <Skeleton className="col-span-full h-3.5 w-[55%]" />
                    </div>
                ))}
            </div>
        );
    }
    const shown = (entries ?? []).filter(
        (e) =>
            e.version > 1 &&
            ("autonomy_level" in e.diff || ASK_FIELD in e.diff),
    );
    if (shown.length === 0) {
        return (
            <div className="st-group">
                <p className="px-4 py-3 text-subheadline text-label-secondary">
                    {t("historyEmpty")}
                </p>
            </div>
        );
    }
    return (
        <ol className="st-group" data-testid="autonomy-changes">
            {shown.map((entry, i) => (
                <li
                    key={entry.id}
                    className="st-activity-row"
                    data-version={entry.version}
                >
                    <span className="st-activity-head">
                        <span>
                            {t("version", {
                                v: formatFixed(entry.version, 0, lang),
                            })}
                        </span>
                        {i === 0 && (
                            <span className="st-badge-accent">
                                {t("current")}
                            </span>
                        )}
                    </span>
                    <time
                        dateTime={entry.created_at}
                        className="st-activity-time"
                    >
                        {formatDayTime(entry.created_at, lang, true)}
                    </time>
                    <span className="st-activity-lines">
                        <ChangeLine entry={entry} />
                    </span>
                </li>
            ))}
        </ol>
    );
}

function LoadError({ onRetry }: { onRetry: () => void }) {
    const { t } = useTranslation("settings-autonomy");
    return (
        <section
            role="alert"
            className="st-group st-empty"
            data-testid="autonomy-error"
        >
            <span className="st-empty-icon is-error" aria-hidden="true">
                <CircleAlert strokeWidth={1.5} />
            </span>
            <h2 className="text-title-3">{t("err.title")}</h2>
            <p className="max-w-[46ch] text-subheadline text-label-secondary">
                {t("err.body")}
            </p>
            <Button variant="secondary" className="mt-2" onClick={onRetry}>
                {t("err.retry")}
            </Button>
        </section>
    );
}

interface SheetState {
    level: number;
    raise: boolean;
    initialAsk: string;
}

export function AutonomyPage() {
    const { t } = useTranslation("settings-autonomy");
    const { t: tAu } = useTranslation("autonomy");
    const lang = useLang();
    const client = useQueryClient();
    const query = useSettings("demo");
    const history = useSettingsHistory("demo");
    const preview = useRiskPreview("env=demo", "demo");
    const save = useSaveSettings("demo");
    const doc = query.data?.data;

    const [sheet, setSheet] = useState<SheetState | null>(null);
    const [sheetError, setSheetError] = useState<string | undefined>();
    const [sheetNotice, setSheetNotice] = useState<SheetNotice>(null);
    /** The amount being edited inline at level 3; null = the saved amount. */
    const [askEdit, setAskEdit] = useState<string | null>(null);
    const [askError, setAskError] = useState<string | undefined>();
    const [busyLevel, setBusyLevel] = useState<number | null>(null);
    const [flash, setFlash] = useState(0);
    const levelsRef = useRef<HTMLDivElement>(null);
    /** After the sheet goes, focus returns to the card that was chosen (design §4.4). */
    const focusCard = (n: number) =>
        requestAnimationFrame(() =>
            levelsRef.current
                ?.querySelector<HTMLButtonElement>(
                    `[role="radio"][data-value="${n}"]`,
                )
                ?.focus(),
        );

    const largestRaw = preview.data?.data.max_notional_usd ?? null;
    const largest = largestRaw === null ? null : Number(largestRaw);
    const money = (v: string | number) => formatMoney(v, "USD", lang);

    const saved = (next: SettingsDoc, title: string, body: string) => {
        setFlash((f) => f + 1);
        toast({ tone: "success", title, body });
        void client.invalidateQueries({ queryKey: historyKey("demo") });
        return next;
    };

    const conflict = async (err: ApiError): Promise<number | null> => {
        const fresh = await query.refetch();
        void client.invalidateQueries({ queryKey: historyKey("demo") });
        return (
            fresh.data?.data.version ??
            (err.body as { current_version?: number } | null)
                ?.current_version ??
            null
        );
    };

    /** Lowering (or 1 → 2), or a lower amount at level 3: saves at once. */
    const saveNow = (
        changes: SettingsChanges,
        onOk: (next: SettingsDoc) => void,
        onFieldError?: (msg: string) => void,
    ) => {
        if (!doc) return;
        save.mutate(
            { version: doc.version, changes },
            {
                onSuccess: onOk,
                onError: async (err) => {
                    setBusyLevel(null);
                    const field = fieldErrors(err)[ASK_FIELD];
                    if (field && onFieldError) return onFieldError(field);
                    if (err instanceof ApiError && err.status === 409)
                        await conflict(err);
                    const level =
                        client.getQueryData<{ data: SettingsDoc }>(
                            settingsKey("demo"),
                        )?.data.settings.autonomy_level ??
                        doc.settings.autonomy_level;
                    toast({
                        tone: "error",
                        title: t("t.fail"),
                        body: t("t.failBody", {
                            n: formatFixed(level, 0, lang),
                        }),
                    });
                },
            },
        );
    };

    const choose = (n: number) => {
        if (!doc || save.isPending) return;
        const current = doc.settings.autonomy_level;
        if (n === current) return;
        if (n >= 3 && n > current) {
            setSheetError(undefined);
            setSheetNotice(null);
            setSheet({
                level: n,
                raise: false,
                initialAsk: askInputText(savedAsk(doc)),
            });
            return;
        }
        setBusyLevel(n);
        setAskEdit(null);
        setAskError(undefined);
        saveNow({ autonomy_level: n }, (next) => {
            setBusyLevel(null);
            const v = formatFixed(next.version, 0, lang);
            const body =
                n === 3
                    ? t("t.low3", { amount: money(savedAsk(next)), v })
                    : t(`t.low${n}`, { v });
            saved(next, t("t.level", { n: formatFixed(n, 0, lang) }), body);
        });
    };

    const onSaveAmount = () => {
        if (!doc || askEdit === null || save.isPending) return;
        const value = parseAsk(askEdit);
        if (Number.isNaN(value)) {
            setAskError(tAu("err.empty"));
            return;
        }
        if (value > Number(savedAsk(doc))) {
            setSheetError(undefined);
            setSheetNotice(null);
            setSheet({ level: 3, raise: true, initialAsk: askEdit });
            return;
        }
        saveNow(
            { [ASK_FIELD]: askApiValue(askEdit) },
            (next) => {
                setAskEdit(null);
                setAskError(undefined);
                saved(
                    next,
                    t("t.amount"),
                    t("t.amountBody", {
                        amount: money(savedAsk(next)),
                        v: formatFixed(next.version, 0, lang),
                    }),
                );
            },
            setAskError,
        );
    };

    const onConfirmSheet = (askText: string) => {
        if (!doc || !sheet) return;
        const { level, raise } = sheet;
        const changes: SettingsChanges = raise ? {} : { autonomy_level: level };
        if (level === 3) changes[ASK_FIELD] = askApiValue(askText);
        setSheetNotice(null);
        save.mutate(
            { version: doc.version, changes },
            {
                onSuccess: (next) => {
                    setSheet(null);
                    focusCard(level);
                    setAskEdit(null);
                    setAskError(undefined);
                    const v = formatFixed(next.version, 0, lang);
                    if (raise)
                        saved(
                            next,
                            t("t.amount"),
                            t("t.amountBody", {
                                amount: money(savedAsk(next)),
                                v,
                            }),
                        );
                    else
                        saved(
                            next,
                            t("t.on", { n: formatFixed(level, 0, lang) }),
                            level === 3
                                ? t("t.on3", {
                                      amount: money(savedAsk(next)),
                                      v,
                                  })
                                : t("t.on4", { v }),
                        );
                },
                onError: async (err) => {
                    const field = fieldErrors(err)[ASK_FIELD];
                    if (field) return setSheetError(field);
                    if (err instanceof ApiError && err.status === 409) {
                        const v = await conflict(err);
                        if (v !== null)
                            return setSheetNotice({
                                kind: "conflict",
                                version: v,
                            });
                    }
                    setSheetNotice({ kind: "server" });
                },
            },
        );
    };

    let body: ReactNode;
    if (query.isPending) {
        body = (
            <div
                className="grid gap-7"
                aria-busy="true"
                data-testid="autonomy-loading"
            >
                <Skeleton className="h-[188px] rounded-card" />
                <div className="grid gap-2">
                    {[0, 1, 2, 3].map((i) => (
                        <Skeleton key={i} className="h-[72px] rounded-card" />
                    ))}
                </div>
            </div>
        );
    } else if (!doc) {
        body = <LoadError onRetry={() => void query.refetch()} />;
    } else {
        const level = doc.settings.autonomy_level;
        const ask = Number(savedAsk(doc));
        const choice = sheet?.level ?? busyLevel ?? level;
        const editText = askEdit ?? askInputText(savedAsk(doc));
        const dirty = askEdit !== null && parseAsk(askEdit) !== ask;
        const timeout = Number(
            doc.settings.approval_timeout_minutes ??
                DEFAULT_APPROVAL_TIMEOUT_MINUTES,
        );
        const field =
            level === 3 && choice === 3 ? (
                <AskField
                    id="au-ask"
                    text={editText}
                    error={askError}
                    disabled={save.isPending}
                    onText={(text) => {
                        setAskEdit(text);
                        setAskError(undefined);
                    }}
                    onEnter={dirty ? onSaveAmount : undefined}
                    actions={
                        dirty ? (
                            <>
                                <Button
                                    variant="plain"
                                    size="sm"
                                    disabled={save.isPending}
                                    onClick={() => {
                                        setAskEdit(null);
                                        setAskError(undefined);
                                    }}
                                >
                                    {tAu("revertAmount")}
                                </Button>
                                <Button
                                    variant="primary"
                                    size="sm"
                                    loading={save.isPending && !sheet}
                                    onClick={onSaveAmount}
                                    data-testid="au-save-amount"
                                >
                                    {tAu("saveAmount")}
                                </Button>
                            </>
                        ) : undefined
                    }
                />
            ) : undefined;

        body = (
            <>
                <Section id="au-now-title" title={t("now")}>
                    <div
                        key={flash}
                        className={cx(
                            "st-group au-now",
                            flash > 0 && "is-flash",
                        )}
                        data-testid="autonomy-now"
                        data-level={level}
                    >
                        <p className="au-now-head">
                            <span
                                className="au-now-tile tabular-nums"
                                aria-hidden="true"
                            >
                                {formatFixed(level, 0, lang)}
                            </span>
                            <span>
                                {t("levelN", {
                                    n: formatFixed(level, 0, lang),
                                })}
                            </span>
                            <LevelName level={level} />
                        </p>
                        <p
                            className="au-now-sentence"
                            data-testid="autonomy-sentence"
                        >
                            <AskSentence
                                level={level}
                                ask={ask}
                                largest={largest}
                            />
                        </p>
                        <AskLine
                            level={level}
                            ask={ask}
                            largest={largest}
                            testId="autonomy-line"
                        />
                        {largest !== null && (
                            <p className="au-now-note">
                                {fill(t("largestNote"), {
                                    largest: <MoneyFig value={largest} />,
                                    link: (
                                        <Link
                                            className="au-link"
                                            to="/settings/money"
                                        >
                                            {t("moneyLink")}
                                        </Link>
                                    ),
                                })}
                            </p>
                        )}
                    </div>
                </Section>

                <Section
                    id="au-levels-title"
                    title={t("levels")}
                    note={t("levelsNote")}
                >
                    <div ref={levelsRef}>
                        <AutonomyPicker
                            value={choice}
                            onChange={choose}
                            enabled={enabledAutonomyLevels(doc)}
                            savedLevel={level}
                            busyLevel={busyLevel}
                            extra={field}
                        />
                    </div>
                </Section>

                <Section id="au-always-title" title={t("always")}>
                    <div className="st-group au-rows">
                        {(
                            [
                                [ShieldCheck, "a1"],
                                [Shield, "a2"],
                                [OctagonX, "a3"],
                            ] as const
                        ).map(([Icon, key]) => (
                            <div key={key} className="au-row">
                                <span
                                    className="au-row-icon"
                                    aria-hidden="true"
                                >
                                    <Icon strokeWidth={1.75} />
                                </span>
                                <span className="au-row-label">{t(key)}</span>
                            </div>
                        ))}
                        <Link
                            to="/settings/notifications"
                            className="au-row is-link"
                        >
                            <span className="au-row-icon" aria-hidden="true">
                                <Bell strokeWidth={1.75} />
                            </span>
                            <span className="au-row-label">{t("a4")}</span>
                            <span className="au-row-value">{t("a4link")}</span>
                            <ChevronRight
                                aria-hidden="true"
                                className="au-row-chev rtl:-scale-x-100"
                                strokeWidth={2}
                            />
                        </Link>
                    </div>
                </Section>

                <Section
                    id="au-waits-title"
                    title={t("waits")}
                    note={t("waitsNote")}
                >
                    <div className="st-group au-rows">
                        <div className="au-row">
                            <span className="au-row-label">{t("window")}</span>
                            <span className="au-row-value is-strong">
                                {fill(t("windowV"), {
                                    n: (
                                        <bdi dir="ltr" className="tabular-nums">
                                            {formatFixed(timeout, 0, lang)}
                                        </bdi>
                                    ),
                                })}
                            </span>
                        </div>
                        <div className="au-row">
                            <span className="au-row-label">{t("timeout")}</span>
                            <span className="au-row-value is-strong">
                                {t("timeoutV")}
                            </span>
                        </div>
                    </div>
                </Section>

                <Section
                    id="au-hist-title"
                    title={t("history")}
                    note={t("historyNote")}
                >
                    <Changes
                        entries={history.data?.data}
                        loading={history.isPending}
                    />
                </Section>

                {sheet && (
                    <TurnOnSheet
                        open
                        level={sheet.level}
                        raise={sheet.raise}
                        mode="settings"
                        initialAsk={sheet.initialAsk}
                        largest={largest}
                        pending={save.isPending}
                        fieldError={sheetError}
                        notice={sheetNotice}
                        onAskEdited={() => setSheetError(undefined)}
                        onCancel={() => {
                            focusCard(sheet.level);
                            setSheet(null);
                            setSheetError(undefined);
                            setSheetNotice(null);
                        }}
                        onConfirm={onConfirmSheet}
                    />
                )}
            </>
        );
    }

    return (
        <div className="st-page st-risk au-page">
            <PaneHeader
                version={doc && !query.isPending ? doc.version : null}
            />
            {body}
        </div>
    );
}

function LevelName({ level }: { level: number }) {
    const { t } = useTranslation("onboarding");
    return <span className="au-now-name">{t(`au.l${level}`)}</span>;
}
