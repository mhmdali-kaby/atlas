import { useQueryClient } from '@tanstack/react-query'
import { ArrowUp, AtSign, CornerUpLeft, X } from 'lucide-react'
import { useEffect, useId, useLayoutEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { formatFixed } from '../../lib/format'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import { useLang } from '../portfolio/hooks'
import { agentStatus, filterAgents, type AgentDef } from './agents'
import type { WarroomMessage, WarroomThread } from './api'
import { drafts, sendMessage } from './live'
import { Medallion } from './Medallion'
import { useAuthorName } from './shape'
import { Excerpt } from './text'

/*
 * The composer (design v4 War Room §7 Composer, MentionPicker, ReplyChip): a floating glass
 * capsule over the feed's bottom edge. Return sends, Shift-Return adds a line, Return while an IME
 * is composing does not send. "@" at the start of a word opens the agent picker; focus stays in
 * the textarea (aria-activedescendant). `/` is plain text in patch 5. The 4,000-character limit is
 * the API's (counted in characters, as Laravel's max: rule does); pasted text is never cut.
 *
 * ATL-42: a ReplyChip above the text sets `reply_to` on send. Esc cancels, but closes the picker
 * first when both are open (it stops propagation on its own branch below).
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
  const ref = useRef<HTMLTextAreaElement>(null)
  const caretAfter = useRef<number | null>(null)

  const chars = Array.from(value).length
  const over = chars > MAX_CHARS
  const canSend = !disabled && Boolean(thread) && value.trim().length > 0 && !over

  const matches = picker ? filterAgents(picker.query, (a) => t(`agents.${a.slug}.name`)) : []
  const pickIndex = picker && matches.length ? Math.min(picker.index, matches.length - 1) : 0
  const listId = `${ids}-picker`
  const optionId = (a: AgentDef) => `${ids}-pk-${a.slug}`

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

  function update(next: string, caret: number) {
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
            aria-expanded={Boolean(picker)}
            aria-controls={picker ? listId : undefined}
            aria-activedescendant={picker && matches.length ? optionId(matches[pickIndex]!) : undefined}
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
        <span>{disabled ? t('composer.cantSend') : <Trans t={t} i18nKey="composer.hint" components={{ k: <kbd /> }} />}</span>
        <span className={['composer-count', 'num', over ? 'is-over' : chars >= NEAR_FROM && 'is-near'].filter(Boolean).join(' ')} aria-live="polite" data-testid="wr-count">
          {count}
        </span>
      </p>
    </div>
  )
}
