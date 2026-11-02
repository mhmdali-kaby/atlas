import {
  AppWindow,
  Bell,
  CircleCheck,
  CircleDashed,
  CircleSlash,
  CloudOff,
  Eye,
  MonitorDot,
  RefreshCw,
  Send,
  TriangleAlert,
} from 'lucide-react'
import { Tooltip } from 'radix-ui'
import { Fragment, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed } from '../../lib/format'
import { Button, Skeleton, Toggle, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { useSettings, type SettingsDoc } from '../onboarding'
import { useLang } from '../portfolio/hooks'
import '../settings-connections/settings.css'
import '../settings-risk/risk.css'
import {
  CHANNELS,
  masked,
  NEEDS,
  prefKey,
  prefsFrom,
  useSaveSwitches,
  useTelegram,
  type Channel,
  type Prefs,
  type TelegramState,
} from './model'
import { EVENTS, GROUPS, iso, type EventId } from '../notifications/events'
import { useBrowserPermission, type Permission } from '../notifications/browser'
import './notifications-settings.css'
import { EventTile } from './parts'
import { Preview } from './Preview'
import { TelegramSection } from './Telegram'

/*
 * Settings → Notifications (brief §14.9; design v18 §6). The 11 §17 events × In Atlas, Browser and
 * Telegram. Each switch saves on its own: one PATCH /api/settings with notifications.<event>.<channel>,
 * shown at once and rolled back with a toast if the save fails. The Telegram column is disabled,
 * keeping its stored values, until a bot is saved.
 */

const NS = 'settings-notifications'
const ENV: SettingsDoc['env'] = 'demo'
const CH_ICON = { in_app: AppWindow, browser: MonitorDot, telegram: Send }

function Tip({ text, children }: { text: string; children: ReactNode }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="top"
          sideOffset={6}
          className="z-70 max-w-[260px] rounded-card px-3 py-1.5 text-footnote text-label vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)]"
        >
          {text}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
}

function ChannelHead({
  ch,
  permission,
  onAllow,
  telegram,
}: {
  ch: Channel
  permission: Permission
  onAllow: () => void
  telegram: TelegramState | undefined
}) {
  const { t } = useTranslation(NS)
  const Icon = CH_ICON[ch]
  let state: ReactNode = null
  if (ch === 'in_app') {
    state = (
      <span className="ns-ch-state is-ok">
        <CircleCheck aria-hidden="true" strokeWidth={1.75} />
        <span>{t('st.inApp')}</span>
      </span>
    )
  } else if (ch === 'browser') {
    state =
      permission === 'granted' ? (
        <span className="ns-ch-state is-ok">
          <CircleCheck aria-hidden="true" strokeWidth={1.75} />
          <span>{t('st.allowed')}</span>
        </span>
      ) : permission === 'denied' ? (
        <Tip text={t('st.blockedTip')}>
          <span className="ns-ch-state is-warn" tabIndex={0} aria-describedby="ns-blocked-tip">
            <TriangleAlert aria-hidden="true" strokeWidth={1.75} />
            <span>{t('st.blocked')}</span>
            <span id="ns-blocked-tip" className="sr-only">
              {t('st.blockedTip')}
            </span>
          </span>
        </Tip>
      ) : permission === 'unsupported' ? (
        <span className="ns-ch-state">
          <CircleSlash aria-hidden="true" strokeWidth={1.75} />
          <span>{t('st.unsupported')}</span>
        </span>
      ) : (
        <>
          <span className="ns-ch-state is-warn">
            <CircleDashed aria-hidden="true" strokeWidth={1.75} />
            <span>{t('st.notAllowed')}</span>
          </span>
          <Button size="sm" variant="secondary" className="ns-xs" onClick={onAllow} data-testid="ns-allow">
            {t('st.allow')}
          </Button>
        </>
      )
  } else {
    state = telegram?.configured ? (
      <span className="ns-ch-state is-ok">
        <CircleCheck aria-hidden="true" strokeWidth={1.75} />
        <span>
          {t('st.tgBot', {
            last4: masked(telegram.bot_token_last4 ?? ''),
          })}
        </span>
      </span>
    ) : (
      <>
        <span className="ns-ch-state" id="ns-tg-none">
          <CircleDashed aria-hidden="true" strokeWidth={1.75} />
          <span>{t('st.tgNone')}</span>
        </span>
        <Button
          size="sm"
          variant="secondary"
          className="ns-xs"
          onClick={() => {
            document.getElementById('ns-tg')?.scrollIntoView({ block: 'start' })
            document.getElementById('ns-tg-token')?.focus({ preventScroll: true })
          }}
        >
          {t('st.tgSetup')}
        </Button>
      </>
    )
  }
  return (
    <div className="ns-ch" role="columnheader">
      <span className="ns-ch-name">
        <Icon aria-hidden="true" strokeWidth={1.5} />
        <span>{t(`ch.${ch}`)}</span>
      </span>
      {state}
    </div>
  )
}

function Matrix({
  prefs,
  busy,
  onToggle,
  selected,
  onSelect,
  permission,
  onAllow,
  telegram,
}: {
  prefs: Prefs
  busy: ReadonlySet<string>
  onToggle: (event: EventId, ch: Channel, on: boolean) => void
  selected: EventId
  onSelect: (e: EventId) => void
  permission: Permission
  onAllow: () => void
  telegram: TelegramState | undefined
}) {
  const { t } = useTranslation(NS)
  const tgReady = telegram?.configured === true
  return (
    <div className="ns-matrix" role="table" aria-label={t('matrix')} data-testid="ns-matrix">
      <div className="ns-head" role="row">
        <div className="ns-ev-head" role="columnheader">
          {t('event')}
        </div>
        {CHANNELS.map((ch) => (
          <ChannelHead key={ch} ch={ch} permission={permission} onAllow={onAllow} telegram={telegram} />
        ))}
      </div>
      {GROUPS.map((g) => (
        <Fragment key={g}>
          <div className="ns-grp" role="row">
            <span role="rowheader">{t(`group.${g}`)}</span>
          </div>
          {EVENTS.filter((e) => e.group === g).map((e) => {
            const name = t(`ev.${e.id}`)
            return (
              <div key={e.id} className={cx('ns-row', selected === e.id && 'is-selected')} role="row" data-testid={`ns-row-${e.id}`}>
                <div className="ns-ev" role="rowheader">
                  <EventTile event={e.id} tone={e.tone} />
                  <span className="ns-ev-text">
                    <span className="ns-ev-name">{name}</span>
                    <span className="ns-ev-desc">{t(`desc.${e.id}`)}</span>
                  </span>
                  <Tip text={t('pv.select')}>
                    <button
                      type="button"
                      className="ns-pv-btn"
                      aria-pressed={selected === e.id}
                      aria-label={t('previewOf', {
                        event: name,
                      })}
                      onClick={() => onSelect(e.id)}
                    >
                      <Eye aria-hidden="true" strokeWidth={1.5} />
                    </button>
                  </Tip>
                </div>
                {CHANNELS.map((ch) => {
                  const k = prefKey(e.id, ch)
                  const disabled = ch === 'telegram' && !tgReady
                  const toggle = (
                    <Toggle
                      className="ns-toggle"
                      checked={prefs[k] === true}
                      disabled={disabled}
                      aria-busy={busy.has(k) || undefined}
                      aria-label={t('sw', {
                        event: name,
                        channel: t(`ch.${ch}`),
                      })}
                      aria-describedby={disabled ? `ns-tg-off-${e.id}` : undefined}
                      onCheckedChange={(on) => onToggle(e.id, ch, on)}
                      data-testid={`sw-${e.id}-${ch}`}
                    />
                  )
                  const tip = disabled ? t('tgOffTip') : ch === 'browser' && prefs[k] && permission !== 'granted' ? t('permOffTip') : null
                  return (
                    <div key={ch} className="ns-cell" role="cell" data-ch={ch}>
                      {tip ? (
                        <Tip text={tip}>
                          <span className="ns-sw" tabIndex={disabled ? 0 : undefined}>
                            {toggle}
                          </span>
                        </Tip>
                      ) : (
                        <span className="ns-sw">{toggle}</span>
                      )}
                      <span className="ns-cell-label" aria-hidden="true">
                        {t(`ch.${ch}`)}
                      </span>
                      {disabled && (
                        <span id={`ns-tg-off-${e.id}`} className="sr-only" data-testid={`tg-off-${e.id}`}>
                          {t('tgOffTip')}
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>
            )
          })}
        </Fragment>
      ))}
    </div>
  )
}

function Header({ version }: { version?: number }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  return (
    <header className="st-pane st-risk-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <Bell strokeWidth={1.5} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav')}</h2>
        <p className="max-w-[54ch] text-subheadline text-label-secondary">{t('intro')}</p>
      </div>
      {version !== undefined && (
        <span className="st-version" data-testid="settings-version">
          {t('version', { v: iso(formatFixed(version, 0, lang)) })}
        </span>
      )}
    </header>
  )
}

export function NotificationsPage() {
  const { t } = useTranslation(NS)
  const settings = useSettings(ENV, true, false)
  const telegram = useTelegram()
  const { permission, ask } = useBrowserPermission()
  const save = useSaveSwitches(ENV)
  /** Switches shown before the server answers, keyed by setting name. */
  const [pending, setPending] = useState<Record<string, boolean>>({})
  const [selected, setSelected] = useState<EventId>('proposal_awaiting_approval')

  const doc = settings.data?.data
  const saved = useMemo(() => (doc ? prefsFrom(doc) : {}), [doc])
  const prefs = useMemo(() => ({ ...saved, ...pending }), [saved, pending])
  const busy = useMemo(() => new Set(Object.keys(pending)), [pending])

  const send = (changes: Record<string, boolean>, eventForToast: EventId) => {
    setPending((p) => ({ ...p, ...changes }))
    const done = () =>
      setPending((p) => {
        const next = { ...p }
        for (const k of Object.keys(changes)) if (next[k] === changes[k]) delete next[k]
        return next
      })
    save.mutate(changes, {
      onSuccess: done,
      onError: () => {
        done()
        toast({
          tone: 'error',
          title: t('t.saveFail'),
          body: t('t.saveFailBody', {
            event: t(`ev.${eventForToast}`),
          }),
        })
      },
    })
  }

  const onToggle = (event: EventId, ch: Channel, on: boolean) => {
    setSelected(event)
    send({ [prefKey(event, ch)]: on }, event)
  }

  const useNeeds = () => send(Object.fromEntries(NEEDS.map((e) => [prefKey(e, 'telegram'), true])), NEEDS[0] as EventId)
  const needsOn = NEEDS.every((e) => prefs[prefKey(e, 'telegram')] === true)

  if (settings.isPending || telegram.isPending) {
    return (
      <div className="st-page ns-page" aria-busy="true" data-testid="ns-loading">
        <Header />
        <div className="st-group">
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="flex items-center gap-3 px-4 py-3">
              <Skeleton className="size-7 rounded-[8px]" />
              <Skeleton className="h-3.5 flex-1" />
              <Skeleton className="h-[22px] w-[220px] rounded-full" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (settings.isError || !doc || telegram.isError) {
    return (
      <div className="st-page ns-page">
        <Header />
        <section role="alert" className="st-group st-empty" data-testid="ns-error">
          <span className="st-empty-icon is-error" aria-hidden="true">
            <CloudOff strokeWidth={1.5} />
          </span>
          <h2 className="text-title-3">{t('err.title')}</h2>
          <p className="max-w-[46ch] text-subheadline text-label-secondary">{t('err.body')}</p>
          <Button
            variant="secondary"
            className="mt-2"
            icon={<RefreshCw strokeWidth={1.5} />}
            onClick={() => {
              void settings.refetch()
              void telegram.refetch()
            }}
          >
            {t('err.retry')}
          </Button>
        </section>
      </div>
    )
  }

  const tg = telegram.data?.data

  return (
    <Tooltip.Provider delayDuration={450}>
      <div className="st-page ns-page" data-testid="ns-page">
        <Header version={doc.version} />
        <section className="ns-section" aria-labelledby="ns-m-title">
          <h2 className="ns-group-title" id="ns-m-title">
            {t('matrix')}
          </h2>
          <div className="ns-layout">
            <div className="ns-col">
              <Matrix
                prefs={prefs}
                busy={busy}
                onToggle={onToggle}
                selected={selected}
                onSelect={setSelected}
                permission={permission}
                onAllow={() => void ask()}
                telegram={tg}
              />
              <p className="ns-group-note">{t('matrixNote')}</p>
            </div>
            <div className="ns-pv-col">
              <Preview
                selected={selected}
                onSelect={setSelected}
                prefs={prefs}
                permission={permission}
                telegramReady={tg?.configured === true}
              />
            </div>
          </div>
        </section>
        <section className="ns-section" id="ns-tg" aria-labelledby="ns-tg-title">
          <h2 className="ns-group-title" id="ns-tg-title">
            {t('tg.title')}
          </h2>
          {tg && <TelegramSection state={tg} onUseNeeds={useNeeds} needsOn={needsOn} />}
          <p className="ns-group-note">{t('tg.note')}</p>
        </section>
      </div>
    </Tooltip.Provider>
  )
}
