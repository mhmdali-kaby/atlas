import { useTranslation } from 'react-i18next'
import { agentBySlug } from './agents'
import type { WarroomMessage, WarroomThread } from './api'
import { baghdadDay } from './time'

/* How a message is drawn and who it is from (design v4 War Room §7, §12). */

const FIVE_MINUTES = 5 * 60_000

export type MessageShape = 'agent' | 'owner' | 'event' | 'alert'

export function shapeOf(m: WarroomMessage): MessageShape {
  if (m.kind === 'system_alert') return 'alert'
  if (m.author_type === 'human') return 'owner'
  if (m.author_type === 'system' && m.kind === 'text') return 'event'
  return 'agent'
}

/** Consecutive messages from the same author, within 5 minutes and on one Baghdad day, drop the head. */
export function continues(m: WarroomMessage, prev: WarroomMessage | undefined): boolean {
  if (!prev || m.reply_to) return false
  const gap = Date.parse(m.created_at) - Date.parse(prev.created_at)
  if (gap < 0 || gap >= FIVE_MINUTES || baghdadDay(m.created_at) !== baghdadDay(prev.created_at)) return false
  if (m.author_type === 'human') return prev.author_type === 'human'
  return prev.author_type === m.author_type && prev.author === m.author && prev.kind === 'text' && m.kind === 'text'
}

export function useAuthorName() {
  const { t } = useTranslation('warroom')
  return (m: WarroomMessage) => {
    if (m.author_type === 'human') return t('you')
    if (m.author_type === 'system') return t('atlas')
    return agentBySlug(m.author) ? t(`agents.${m.author}.name`) : m.author
  }
}

export function useThreadName() {
  const { t } = useTranslation('warroom')
  return (thread: WarroomThread | undefined) => {
    if (!thread) return ''
    if (thread.kind === 'main') return t('main')
    if (thread.kind === 'agent') return t(`agents.${thread.key}.name`, { defaultValue: thread.title })
    return thread.key
  }
}
