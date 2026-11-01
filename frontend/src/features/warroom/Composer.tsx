import { useQueryClient } from '@tanstack/react-query'
import { ArrowUp, AtSign, CornerUpLeft, X } from 'lucide-react'
import { useEffect, useId, useLayoutEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { formatFixed } from '../../lib/format'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import { useLang } from '../portfolio/hooks'
import { agentStatus, filterAgents, type AgentDef } from './agents'
import type { WarroomMessage, WarroomThread } from './api'
import { KillSheet } from '../kill-switch/KillSwitch'
import { ResumeSheet } from '../kill-switch/TradingBanner'
import { drafts, sendMessage } from './live'
import { Medallion } from './Medallion'
import { useAuthorName } from './shape'
import { isSlash, parseSlash, SLASH_COMMANDS, slashMatches, type SlashDef, type SlashName } from './slash'
import { useSlashRunner, type SlashNote } from './slashRun'
import { Excerpt } from './text'

/*
 * The composer (design v4 War Room §7 Composer, MentionPicker, ReplyChip): a floating glass
 * capsule over the feed's bottom edge. Return sends, Shift-Return adds a line, Return while an IME
 * is composing does not send. "@" at the start of a word opens the agent picker; focus stays in
 * the textarea (aria-activedescendant). The 4,000-character limit is
 * the API's (counted in characters, as Laravel's max: rule does); pasted text is never cut.
 *
 * ATL-42: a ReplyChip above the text sets `reply_to` on send. Esc cancels, but closes the picker
 * first when both are open (it stops propagation on its own branch below).
 *
 * ATL-163: a message starting with "/" is a slash command (slash.ts), run by useSlashRunner rather
 * than sent as text. While the text is a single "/word" the same picker lists the matching
 * commands. An unknown command, or a known one with the wrong arguments, shows the command list
 * from the locale file in the picker's place and sends nothing; the text stays for editing.
 */

/** The ReplyChip (design v4 War Room §7): the parent's author, its excerpt, and a cancel button. */
function ReplyChip({ message, onCancel }: { message: WarroomMessage; onCancel: () => void }) {
  const { t } = useTranslation('warroom')
  const authorName = useAuthorName()
  return (
    <div className="composer-reply" data-testid="wr-reply-chip">
      <CornerUpLeft aria-hidden="true" strokeWidth={1.5} className="icon-dir" />
      <div className="composer-reply-text">
        <b>{t('replyChip.title', { name: authorName(message) })}</b>
        <span className="excerpt" dir="auto">
          <Excerpt text={message.content_md} />
        </span>
      </div>
      <button
        type="button"
        className="composer-reply-cancel"
        aria-label={t('replyChip.cancel')}
        title={t('replyChip.cancel')}
        onClick={onCancel}
        data-testid="wr-reply-cancel"
      >
        <X aria-hidden="true" strokeWidth={1.5} />
      </button>
    </div>
  )
}

type SlashHelp = { kind: 'unknown'; typed: string } | { kind: 'usage'; name: SlashName }

/** The inline help (ATL-163): what was wrong, then every command with what it does. */
function SlashHelpPanel({ help }: { help: SlashHelp }) {
  const { t } = useTranslation('warroom')
  const usage = help.kind === 'usage' ? SLASH_COMMANDS.find((c) => c.name === help.name) : undefined
  return (
    <div className="picker" role="status" data-testid="wr-slash-help">
      <p className="picker-label" dir="auto">
        {help.kind === 'unknown' ? (
          t('slash.help.unknown', { cmd: help.typed })
        ) : (
          <Trans t={t} i18nKey="slash.help.usage" values={{ usage: usage?.label ?? '' }} components={{ c: <bdi dir="ltr" /> }} />
        )}
      </p>
      {SLASH_COMMANDS.map((c) => (
        <div key={c.name} className="picker-item">
          <span className="pk-text">
            <bdi className="pk-name" dir="ltr">
              {c.label}
            </bdi>
            <span className="pk-sub">{t(`slash.cmd.${c.name}`)}</span>
          </span>
        </div>
      ))}
    </div>
  )
}

const MAX_CHARS = 4000
const COUNT_FROM = 3500
const NEAR_FROM = 3600
const MAX_LINES = 8

interface Picker {
  /** Index of the "@" in the text. */
  start: number
  query: string
  index: number
}

/** The "@word" right before the caret, if any. */
function mentionAt(text: string, caret: number): { start: number; query: string } | null {
  const m = /(^|\s)@([A-Za-z]*)$/.exec(text.slice(0, caret))
  return m ? { start: caret - m[2]!.length - 1, query: m[2]! } : null
}

export interface ComposerProps {
  env: TradingEnv
  thread: WarroomThread | undefined
  threadName: string
  /** The thread failed to load or is still loading. */
  disabled: boolean
  /** The message being replied to (ATL-42), or null for a plain message. */
  replyTo: WarroomMessage | null
  onCancelReply: () => void
}

export function Composer({ env, thread, threadName, disabled, replyTo, onCancelReply }: ComposerProps) {
  const { t } = useTranslation('warroom')
  const lang = useLang()
  const qc = useQueryClient()
  const ids = useId()
  const draftKey = thread ? `${env}:${thread.id}` : ''
  const [value, setValue] = useState(() => drafts.get(draftKey) ?? '')
  const [picker, setPicker] = useState<Picker | null>(null)
  const [slashIndex, setSlashIndex] = useState(0)
  const [slashClosed, setSlashClosed] = useState(false)
  const [help, setHelp] = useState<SlashHelp | null>(null)
  const [note, setNote] = useState<SlashNote | null>(null)
  const slash = useSlashRunner(env, thread)
  const ref = useRef<HTMLTextAreaElement>(null)
  const caretAfter = useRef<number | null>(null)

  const chars = Array.from(value).length
  const over = chars > MAX_CHARS
  const canSend = !disabled && Boolean(thread) && value.trim().length > 0 && !over

  const matches = picker ? filterAgents(picker.query, (a) => t(`agents.${a.slug}.name`)) : []
  const pickIndex = picker && matches.length ? Math.min(picker.index, matches.length - 1) : 0
  const listId = `${ids}-picker`
  const optionId = (a: AgentDef) => `${ids}-pk-${a.slug}`
  const slashList = !picker && !slashClosed && !disabled ? slashMatches(value) : []
  const slashPick = slashList.length ? Math.min(slashIndex, slashList.length - 1) : 0
  const slashListId = `${ids}-slash`
  const slashOptionId = (c: SlashDef) => `${ids}-sl-${c.name}`
  const listOpen = Boolean(picker) || slashList.length > 0

  // Put the caret where an insertion asked for it, after React has written the value.
  useLayoutEffect(() => {
    const at = caretAfter.current
    const ta = ref.current
    if (at === null || !ta) return
    caretAfter.current = null
    ta.focus()
    ta.setSelectionRange(at, at)
  })

  // Browsers without `field-sizing: content` grow the textarea by hand, up to 8 lines.
  useLayoutEffect(() => {
    const ta = ref.current
    if (!ta || CSS.supports?.('field-sizing', 'content')) return
    ta.style.height = 'auto'
    const lh = parseFloat(getComputedStyle(ta).lineHeight) || 23
    ta.style.height = `${Math.min(ta.scrollHeight, lh * MAX_LINES + 14)}px`
  }, [value])

  // Reply puts the ReplyChip in the composer and focuses it (design v4 War Room §7).
  useEffect(() => {
    if (replyTo) ref.current?.focus()
  }, [replyTo])

  // A click outside the composer closes the picker.
  const wrapRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!picker) return
    const onDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setPicker(null)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [picker])

  // The ReplyChip grows .composer-wrap, so the floating jump-to-latest/new-messages row (ATL-39,
  // a sibling under .wr-pane, positioned from its own bottom edge) must track that height rather
  // than assume a fixed one, or it lands on top of the chip's × (ATL-42 QA). --wr-composer-h is
  // read by .wr-float in warroom.css; its fallback matches .composer-wrap's own default height.
  useLayoutEffect(() => {
    const wrap = wrapRef.current
    const pane = wrap?.closest<HTMLElement>('.wr-pane')
    if (!wrap || !pane || typeof ResizeObserver === 'undefined') return
    const set = (h: number) => pane.style.setProperty('--wr-composer-h', `${h}px`)
    set(wrap.getBoundingClientRect().height)
    // Reads the size the browser already computed for this notification (borderBoxSize) rather
    // than calling getBoundingClientRect() again inside the callback, which forces a fresh
    // layout and can trigger the browser's "ResizeObserver loop" warning.
    const ro = new ResizeObserver(([entry]) => set(entry?.borderBoxSize?.[0]?.blockSize ?? wrap.getBoundingClientRect().height))
    ro.observe(wrap)
    return () => ro.disconnect()
  }, [])

  function update(next: string, caret: number) {
    if (next !== value) {
      setHelp(null)
      setNote(null)
      setSlashClosed(false)
      if (!slashMatches(next).length) setSlashIndex(0)
    }
    setValue(next)
    if (draftKey) drafts.set(draftKey, next)
    const m = mentionAt(next, caret)
    setPicker(m ? { ...m, index: picker?.index ?? 0 } : null)
  }

  function onChange(e: ChangeEvent<HTMLTextAreaElement>) {
    update(e.target.value, e.target.selectionStart)
  }

  function onSelect() {
    const ta = ref.current
    if (!ta) return
    const m = mentionAt(ta.value, ta.selectionStart)
    if (!m) setPicker(null)
    else if (!picker || picker.start !== m.start) setPicker({ ...m, index: 0 })
  }

  function insert(agent: AgentDef) {
    const ta = ref.current
    if (!ta || !picker) return
    const caret = ta.selectionStart
    const text = `@${agent.handle} `
    const next = value.slice(0, picker.start) + text + value.slice(Math.max(caret, picker.start + 1 + picker.query.length))
    setValue(next)
    if (draftKey) drafts.set(draftKey, next)
    setPicker(null)
    caretAfter.current = picker.start + text.length
  }

  /** Writes the chosen command into the composer (ATL-163 autocomplete). */
  function insertSlash(c: SlashDef) {
    setValue(c.insert)
    if (draftKey) drafts.set(draftKey, c.insert)
    setSlashIndex(0)
    setSlashClosed(true)
    caretAfter.current = c.insert.length
  }

  /** Runs a "/" message. Unknown or malformed: show the help and keep the text. */
  function runSlash(content: string) {
    const parsed = parseSlash(content)
    if (parsed.kind === 'unknown' || parsed.kind === 'usage') {
      setHelp(parsed.kind === 'unknown' ? { kind: 'unknown', typed: parsed.typed } : { kind: 'usage', name: parsed.name })
      setSlashClosed(true)
      return
    }
    setValue('')
    drafts.delete(draftKey)
    setPicker(null)
    setHelp(null)
    setNote(null)
    void slash.run(parsed).then(setNote)
    ref.current?.focus()
  }

  /** The @ button: types "@" at the caret (after a space when needed) and opens the picker. */
  function openPicker() {
    const ta = ref.current
    if (!ta) return
    if (picker) {
      setPicker(null)
      ta.focus()
      return
    }
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const lead = start > 0 && !/\s/.test(value[start - 1]!) ? ' ' : ''
    const next = value.slice(0, start) + lead + '@' + value.slice(end)
    setValue(next)
    if (draftKey) drafts.set(draftKey, next)
    setPicker({ start: start + lead.length, query: '', index: 0 })
    caretAfter.current = start + lead.length + 1
  }

  function send() {
    if (!canSend || !thread) return
    const content = value.trim()
    if (isSlash(content)) {
      runSlash(content)
      return
    }
    const replyToId = replyTo?.id ?? null
    setValue('')
    drafts.delete(draftKey)
    setPicker(null)
    onCancelReply()
    void sendMessage(qc, { env, threadId: thread.id, content, replyTo: replyToId })
    ref.current?.focus()
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    const composing = e.nativeEvent.isComposing || e.keyCode === 229
    if (picker) {
      const n = matches.length
      if (e.key === 'ArrowDown' && n) {
        e.preventDefault()
        setPicker({ ...picker, index: (pickIndex + 1) % n })
        return
      }
      if (e.key === 'ArrowUp' && n) {
        e.preventDefault()
        setPicker({ ...picker, index: (pickIndex - 1 + n) % n })
        return
      }
      if ((e.key === 'Enter' || e.key === 'Tab') && n && !composing) {
        e.preventDefault()
        insert(matches[pickIndex]!)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setPicker(null)
        return
      }
    }
    if (slashList.length) {
      const n = slashList.length
      const hit = slashList[slashPick]!
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSlashIndex((slashPick + 1) % n)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSlashIndex((slashPick - 1 + n) % n)
        return
      }
      // Return on a command already typed in full runs it; otherwise Return/Tab completes it.
      if ((e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && value.trim().toLowerCase() !== hit.insert)) && !composing) {
        e.preventDefault()
        insertSlash(hit)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setSlashClosed(true)
        return
      }
    }
    if (e.key === 'Escape' && help) {
      e.preventDefault()
      e.stopPropagation()
      setHelp(null)
      return
    }
    // Esc cancels a reply once the picker (handled above) is out of the way.
    if (e.key === 'Escape' && replyTo) {
      e.preventDefault()
      onCancelReply()
      return
    }
    if (e.key === 'Enter' && !e.shiftKey && !composing) {
      e.preventDefault()
      send()
    }
  }

  const placeholder =
    thread?.kind === 'instrument'
      ? t('composer.placeholderInstrument', { inst: thread.key })
      : thread?.kind === 'agent'
        ? t('composer.placeholderAgent', { name: threadName })
        : t('composer.placeholder')

  const count = over
    ? t('composer.over', { n: formatFixed(chars - MAX_CHARS, 0, lang), max: formatFixed(MAX_CHARS, 0, lang) })
    : chars >= COUNT_FROM
      ? t('composer.count', { n: formatFixed(chars, 0, lang), max: formatFixed(MAX_CHARS, 0, lang) })
      : ''

  return (
    <div className="composer-wrap" ref={wrapRef}>
      <div className={['composer', over && 'is-over', disabled && 'is-disabled'].filter(Boolean).join(' ')} data-testid="wr-composer">
        {picker && (
          <div className="picker" id={listId} role="listbox" aria-label={t('composer.pickerLabel')} data-testid="wr-picker">
            <p className="picker-label" aria-hidden="true">
              {t('composer.pickerLabel')}
            </p>
            {matches.length ? (
              matches.map((a, i) => (
                <div
                  key={a.slug}
                  id={optionId(a)}
                  role="option"
                  aria-selected={i === pickIndex}
                  className="picker-item"
                  // Keep focus in the textarea: a press must not blur it.
                  onPointerDown={(e) => e.preventDefault()}
                  onClick={() => insert(a)}
                >
                  <Medallion slug={a.slug} size={26} status={agentStatus(a.slug)} />
                  <span className="pk-text">
                    <span className="pk-name">{t(`agents.${a.slug}.name`)}</span>
                    <span className="pk-sub">
                      {t('composer.pickerSub', { status: t(`status.${agentStatus(a.slug)}`), role: t(`agents.${a.slug}.role`) })}
                    </span>
                  </span>
                  <bdi className="pk-handle" dir="ltr">
                    @{a.handle}
                  </bdi>
                </div>
              ))
            ) : (
              <p className="picker-empty">{t('composer.pickerNone', { q: picker.query })}</p>
            )}
          </div>
        )}
        {slashList.length > 0 && (
          <div className="picker" id={slashListId} role="listbox" aria-label={t('slash.pickerLabel')} data-testid="wr-slash-picker">
            <p className="picker-label" aria-hidden="true">
              {t('slash.pickerLabel')}
            </p>
            {slashList.map((c, i) => (
              <div
                key={c.name}
                id={slashOptionId(c)}
                role="option"
                aria-selected={i === slashPick}
                className="picker-item"
                onPointerDown={(e) => e.preventDefault()}
                onClick={() => insertSlash(c)}
              >
                <span className="pk-text">
                  <bdi className="pk-name" dir="ltr">
                    {c.label}
                  </bdi>
                  <span className="pk-sub">{t(`slash.cmd.${c.name}`)}</span>
                </span>
              </div>
            ))}
          </div>
        )}
        {help && !slashList.length && <SlashHelpPanel help={help} />}
        {replyTo && <ReplyChip message={replyTo} onCancel={onCancelReply} />}
        <div className="composer-row">
          <textarea
            ref={ref}
            rows={1}
            dir="auto"
            value={value}
            disabled={disabled}
            placeholder={disabled ? undefined : placeholder}
            aria-label={t('composer.label')}
            aria-describedby={`${ids}-hint`}
            aria-autocomplete="list"
            aria-expanded={listOpen}
            aria-controls={picker ? listId : slashList.length ? slashListId : undefined}
            aria-activedescendant={
              picker && matches.length ? optionId(matches[pickIndex]!) : slashList.length ? slashOptionId(slashList[slashPick]!) : undefined
            }
            aria-invalid={over || undefined}
            onChange={onChange}
            onSelect={onSelect}
            onKeyDown={onKeyDown}
            data-testid="wr-composer-input"
          />
          <button
            type="button"
            className="composer-btn"
            aria-label={t('composer.mention')}
            title={t('composer.mention')}
            aria-expanded={Boolean(picker)}
            disabled={disabled}
            onPointerDown={(e) => e.preventDefault()}
            onClick={openPicker}
          >
            <AtSign aria-hidden="true" strokeWidth={1.5} />
          </button>
          <button
            type="button"
            className="composer-send"
            aria-label={t('composer.send')}
            title={t('composer.send')}
            disabled={!canSend}
            onClick={send}
            data-testid="wr-send"
          >
            <ArrowUp aria-hidden="true" />
          </button>
        </div>
      </div>
      <p className="composer-hint" id={`${ids}-hint`}>
        {note && !disabled ? (
          <span role="status" className={note.tone === 'error' ? 'composer-note is-error' : 'composer-note'} data-testid="wr-slash-note">
            {note.text}
          </span>
        ) : (
          <span>{disabled ? t('composer.cantSend') : <Trans t={t} i18nKey="composer.hint" components={{ k: <kbd /> }} />}</span>
        )}
        <span className={['composer-count', 'num', over ? 'is-over' : chars >= NEAR_FROM && 'is-near'].filter(Boolean).join(' ')} aria-live="polite" data-testid="wr-count">
          {count}
        </span>
      </p>
      <KillSheet open={slash.kill.open} onOpenChange={slash.setKillOpen} initialFlatten={slash.kill.flatten} />
      {slash.health && <ResumeSheet open={slash.resumeOpen} onOpenChange={slash.setResumeOpen} health={slash.health} />}
    </div>
  )
}
