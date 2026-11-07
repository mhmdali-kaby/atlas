import { useCallback, useEffect, useState } from 'react'

/*
 * Browser notifications (design v18 §5.1, decision "Browser notifications only while Atlas is not
 * in front"): a system banner for a row whose Browser switch was on when it was stored, when the
 * owner has granted permission and Atlas is hidden or not focused (a toast already shows it when
 * Atlas is in front). Permission is only ever requested from a button press, never on load.
 */

export type Permission = 'granted' | 'denied' | 'default' | 'unsupported'

export function readPermission(): Permission {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  return window.Notification.permission
}

/** True when Atlas is not the window the owner is looking at. */
export function atlasInBackground(): boolean {
  if (typeof document === 'undefined') return false
  return document.hidden || !document.hasFocus()
}

export function showBrowserNotification(
  input: { title: string; body: string; tag: string; urgent: boolean },
  onClick: () => void,
): boolean {
  if (readPermission() !== 'granted' || !atlasInBackground()) return false
  try {
    const n = new window.Notification(input.title, { body: input.body, tag: input.tag, requireInteraction: input.urgent })
    n.onclick = () => {
      window.focus()
      onClick()
      n.close()
    }
    return true
  } catch {
    // Some browsers only allow notifications from a service worker; the list still has the row.
    return false
  }
}

const HIDDEN_KEY = 'atlas.notifications.permPromptHidden'

function readHidden(): boolean {
  try {
    return localStorage.getItem(HIDDEN_KEY) === '1'
  } catch {
    return false
  }
}

/** The permission, kept current, and the owner's "Not now"/"Hide" on the list's prompt. */
export function useBrowserPermission() {
  const [permission, setPermission] = useState<Permission>(readPermission)
  const [hidden, setHidden] = useState(readHidden)
  useEffect(() => {
    let status: PermissionStatus | undefined
    const refresh = () => setPermission(readPermission())
    void navigator.permissions
      ?.query({ name: 'notifications' as PermissionName })
      .then((s) => {
        status = s
        s.addEventListener('change', refresh)
      })
      .catch(() => undefined)
    return () => status?.removeEventListener('change', refresh)
  }, [])
  const ask = useCallback(async (): Promise<Permission> => {
    if (readPermission() !== 'default') return readPermission()
    await window.Notification.requestPermission()
    const next = readPermission()
    setPermission(next)
    return next
  }, [])
  const hide = useCallback(() => {
    try {
      localStorage.setItem(HIDDEN_KEY, '1')
    } catch {
      // Private mode: hidden for this visit only.
    }
    setHidden(true)
  }, [])
  return { permission, hidden, ask, hide }
}
