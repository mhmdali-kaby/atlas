import { Copy, CornerUpLeft } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from '../../ui'
import type { WarroomMessage } from './api'
import { plainText } from './tokens'

/*
 * MessageActions and its context menu (design v4 War Room §7 "MessageActions and context menu").
 * Only the active message shows the glass capsule, with Reply (mirrored) and Copy text, 28px
 * targets. Right-click, the context-menu key or Shift-F10 on a focused message open the same two
 * items as a menu (the article's own onContextMenu, Message.tsx); while that menu is open the
 * message stays active (Feed's onMenuChange pins it). Copy confirms with the toast "Copied" and
 * copies the text as written: no islands, status tags as words.
 */

/** Copies the message's plain text (no islands, status tags as words); true on success. */
async function copyMessage(message: WarroomMessage, tagLabel: (tag: string) => string): Promise<boolean> {
  const text = plainText(message.content_md, tagLabel)
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

export interface MessageActionsProps {
  message: WarroomMessage
  onReply: () => void
}

/** The hover/focus capsule: shown only for the active message (Message.tsx decides). */
export function MessageActions({ message, onReply }: MessageActionsProps) {
  const { t } = useTranslation('warroom')

  async function copyText() {
    const ok = await copyMessage(message, (tag) => t(`tag.${tag}`))
    if (ok) toast({ title: t('actions.copied') })
  }

  return (
    <div className="msg-actions" data-testid="wr-msg-actions">
      <button type="button" className="msg-action" aria-label={t('actions.reply')} title={t('actions.reply')} onClick={onReply} data-testid="wr-action-reply">
        <CornerUpLeft aria-hidden="true" strokeWidth={1.5} className="icon-dir" />
      </button>
      <button
        type="button"
        className="msg-action"
        aria-label={t('actions.copy')}
        title={t('actions.copy')}
        onClick={() => void copyText()}
        data-testid="wr-action-copy"
      >
        <Copy aria-hidden="true" strokeWidth={1.5} />
      </button>
    </div>
  )
}

export interface MessageMenuProps {
  message: WarroomMessage
  /** "{{name}}, {{time}}" — the same label the message's own article carries. */
  label: string
  x: number
  y: number
  onReply: () => void
  onClose: () => void
}

/** The right-click / context-menu-key / Shift-F10 menu: the same two items, at the pointer. */
export function MessageMenu({ message, label, x, y, onReply, onClose }: MessageMenuProps) {
  const { t } = useTranslation('warroom')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose()
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])

  async function copyText() {
    const ok = await copyMessage(message, (tag) => t(`tag.${tag}`))
    if (ok) toast({ title: t('actions.copied') })
    onClose()
  }

  function reply() {
    onReply()
    onClose()
  }

  return (
    // left/top, not inset-inline-start: x/y come from the pointer event's physical viewport
    // coordinates (clientX/clientY), which are the same in RTL and LTR.
    <div ref={ref} role="menu" aria-label={label} className="msg-menu" style={{ left: x, top: y }} data-testid="wr-msg-menu">
      <button type="button" role="menuitem" className="msg-menu-item" onClick={reply} data-testid="wr-menu-reply">
        <CornerUpLeft aria-hidden="true" strokeWidth={1.5} className="icon-dir" />
        {t('actions.reply')}
      </button>
      <button type="button" role="menuitem" className="msg-menu-item" onClick={() => void copyText()} data-testid="wr-menu-copy">
        <Copy aria-hidden="true" strokeWidth={1.5} />
        {t('actions.copy')}
      </button>
    </div>
  )
}
