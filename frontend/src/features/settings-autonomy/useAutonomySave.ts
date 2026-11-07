import { useQueryClient } from '@tanstack/react-query'
import { useState, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../lib/api'
import { formatFixed, formatMoney } from '../../lib/format'
import { toast } from '../../ui'
import {
  askApiValue,
  askInputText,
  parseAsk,
  settingsKey,
  type SettingsChanges,
  type SettingsDoc,
  type SheetNotice,
  type useSettings,
} from '../onboarding'
import { useLang } from '../portfolio/hooks'
import { fieldErrors, historyKey, useSaveSettings } from '../settings-risk/model'
import { ASK_FIELD, savedAsk } from './model'

export interface SheetState {
  level: number
  /** A higher amount at level 3 (no level change). */
  raise: boolean
  initialAsk: string
}

/**
 * The save rules of Settings → Autonomy (ATL-216, design §4.4). Raising autonomy (to 3 or 4, or a
 * higher amount at 3) opens the turn-on sheet, which saves; lowering saves at once.
 */
export function useAutonomySave(
  query: ReturnType<typeof useSettings>,
  levelsRef: RefObject<HTMLDivElement | null>,
  rowEnv: SettingsDoc['env'] = 'demo',
) {
  const { t } = useTranslation('settings-autonomy')
  const { t: tAu } = useTranslation('autonomy')
  const lang = useLang()
  const client = useQueryClient()
  const save = useSaveSettings(rowEnv)
  const doc = query.data?.data

  const [sheet, setSheet] = useState<SheetState | null>(null)
  const [sheetError, setSheetError] = useState<string | undefined>()
  const [sheetNotice, setSheetNotice] = useState<SheetNotice>(null)
  /** The amount being edited inline at level 3; null = the saved amount. */
  const [askEdit, setAskEdit] = useState<string | null>(null)
  const [askError, setAskError] = useState<string | undefined>()
  const [busyLevel, setBusyLevel] = useState<number | null>(null)
  const [flash, setFlash] = useState(0)
  /** After the sheet goes, focus returns to the card that was chosen (design §4.4). */
  const focusCard = (n: number) =>
    requestAnimationFrame(() => levelsRef.current?.querySelector<HTMLButtonElement>(`[role="radio"][data-value="${n}"]`)?.focus())

  const money = (v: string | number) => formatMoney(v, 'USD', lang)
  const version = (next: SettingsDoc) => formatFixed(next.version, 0, lang)

  const saved = (title: string, body: string) => {
    setFlash((f) => f + 1)
    toast({ tone: 'success', title, body })
    void client.invalidateQueries({ queryKey: historyKey(rowEnv) })
  }

  const conflict = async (err: ApiError): Promise<number | null> => {
    const fresh = await query.refetch()
    void client.invalidateQueries({ queryKey: historyKey(rowEnv) })
    return fresh.data?.data.version ?? (err.body as { current_version?: number } | null)?.current_version ?? null
  }

  const openSheet = (next: SheetState) => {
    setSheetError(undefined)
    setSheetNotice(null)
    setSheet(next)
  }

  const resetAsk = () => {
    setAskEdit(null)
    setAskError(undefined)
  }

  /** Lowering (or 1 → 2), or a lower amount at level 3: saves at once. */
  const saveNow = (changes: SettingsChanges, onOk: (next: SettingsDoc) => void, onFieldError?: (msg: string) => void) => {
    if (!doc) return
    save.mutate(
      { version: doc.version, changes },
      {
        onSuccess: onOk,
        onError: async (err) => {
          setBusyLevel(null)
          const field = fieldErrors(err)[ASK_FIELD]
          if (field && onFieldError) return onFieldError(field)
          if (err instanceof ApiError && err.status === 409) await conflict(err)
          const level = client.getQueryData<{ data: SettingsDoc }>(settingsKey(rowEnv))?.data.settings.autonomy_level ?? doc.settings.autonomy_level
          toast({ tone: 'error', title: t('t.fail'), body: t('t.failBody', { n: formatFixed(level, 0, lang) }) })
        },
      },
    )
  }

  const choose = (n: number) => {
    if (!doc || save.isPending) return
    const current = doc.settings.autonomy_level
    if (n === current) return
    if (n >= 3 && n > current) {
      openSheet({ level: n, raise: false, initialAsk: askInputText(savedAsk(doc)) })
      return
    }
    setBusyLevel(n)
    resetAsk()
    saveNow({ autonomy_level: n }, (next) => {
      setBusyLevel(null)
      const v = version(next)
      const body = n === 3 ? t('t.low3', { amount: money(savedAsk(next)), v }) : t(`t.low${n}`, { v })
      saved(t('t.level', { n: formatFixed(n, 0, lang) }), body)
    })
  }

  const saveAmount = () => {
    if (!doc || askEdit === null || save.isPending) return
    const value = parseAsk(askEdit)
    if (Number.isNaN(value)) {
      setAskError(tAu('err.empty'))
      return
    }
    if (value > Number(savedAsk(doc))) {
      openSheet({ level: 3, raise: true, initialAsk: askEdit })
      return
    }
    saveNow(
      { [ASK_FIELD]: askApiValue(askEdit) },
      (next) => {
        resetAsk()
        saved(t('t.amount'), t('t.amountBody', { amount: money(savedAsk(next)), v: version(next) }))
      },
      setAskError,
    )
  }

  const confirmSheet = (askText: string) => {
    if (!doc || !sheet) return
    const { level, raise } = sheet
    const changes: SettingsChanges = raise ? {} : { autonomy_level: level }
    if (level === 3) changes[ASK_FIELD] = askApiValue(askText)
    setSheetNotice(null)
    save.mutate(
      { version: doc.version, changes },
      {
        onSuccess: (next) => {
          setSheet(null)
          focusCard(level)
          resetAsk()
          const v = version(next)
          if (raise) saved(t('t.amount'), t('t.amountBody', { amount: money(savedAsk(next)), v }))
          else
            saved(
              t('t.on', { n: formatFixed(level, 0, lang) }),
              level === 3 ? t('t.on3', { amount: money(savedAsk(next)), v }) : t('t.on4', { v }),
            )
        },
        onError: async (err) => {
          const errors = fieldErrors(err)
          const field = errors[ASK_FIELD]
          if (field) return setSheetError(field)
          // ATL-238: live autonomy above 2 during the first live month, in the API's own words.
          const refused = errors.autonomy_level
          if (refused) return setSheetNotice({ kind: 'refused', message: refused })
          if (err instanceof ApiError && err.status === 409) {
            const v = await conflict(err)
            if (v !== null) return setSheetNotice({ kind: 'conflict', version: v })
          }
          setSheetNotice({ kind: 'server' })
        },
      },
    )
  }

  const cancelSheet = () => {
    if (sheet) focusCard(sheet.level)
    setSheet(null)
    setSheetError(undefined)
    setSheetNotice(null)
  }

  const editAsk = (text: string) => {
    setAskEdit(text)
    setAskError(undefined)
  }

  return {
    pending: save.isPending,
    sheet,
    sheetError,
    sheetNotice,
    clearSheetError: () => setSheetError(undefined),
    askEdit,
    askError,
    busyLevel,
    flash,
    choose,
    saveAmount,
    confirmSheet,
    cancelSheet,
    editAsk,
    revertAsk: resetAsk,
  }
}
