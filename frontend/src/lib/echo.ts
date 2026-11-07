import Echo, { type ConnectionStatus } from 'laravel-echo'
import Pusher from 'pusher-js'
import { useSyncExternalStore } from 'react'
import { api } from './api'

/*
 * The one Echo client (brief §3.2, §12): Reverb speaks the Pusher protocol, so pusher-js carries it.
 * Private channels are authorised by POST /broadcasting/auth through `api`, which sends the Sanctum
 * session cookie and X-XSRF-TOKEN like every other SPA request (docs/handoff/ATL-37-bridge.md).
 *
 * Configuration comes from VITE_REVERB_*; the defaults match backend/.env.example and `./atlas up`
 * (Reverb on 127.0.0.1:8080, key atlas-local-key, plain ws).
 *
 * The client is created on first use (the War Room), not at import, so signed-out screens never
 * open a socket. Listen with a leading dot: `.warroom.message`.
 */

export type LiveStatus = ConnectionStatus

const env = import.meta.env
const SCHEME = (env.VITE_REVERB_SCHEME as string | undefined) ?? 'http'
const PORT = Number((env.VITE_REVERB_PORT as string | undefined) ?? 8080)

let client: Echo<'reverb'> | null = null
let connectedOnce = false

export function getEcho(): Echo<'reverb'> {
  if (client) return client
  client = new Echo<'reverb'>({
    broadcaster: 'reverb',
    Pusher,
    key: (env.VITE_REVERB_APP_KEY as string | undefined) ?? 'atlas-local-key',
    wsHost: (env.VITE_REVERB_HOST as string | undefined) ?? '127.0.0.1',
    wsPort: PORT,
    wssPort: PORT,
    forceTLS: SCHEME === 'https',
    enabledTransports: ['ws', 'wss'],
    channelAuthorization: {
      customHandler: ({ socketId, channelName }, callback) => {
        api
          .post<{ auth: string; channel_data?: string }>('/broadcasting/auth', { socket_id: socketId, channel_name: channelName })
          .then((data) => callback(null, data))
          .catch((error: unknown) => callback(error instanceof Error ? error : new Error(String(error)), null))
      },
    },
  })
  client.connector.onConnectionChange((status) => {
    if (status === 'connected') connectedOnce = true
  })
  return client
}

/** Whether this page load's socket has ever connected (a first attempt still pending is not "down"). */
export function hasConnected(): boolean {
  return connectedOnce
}

/** Drops the socket and opens a new one now, instead of waiting for pusher-js's back-off. */
export function reconnectEcho(): void {
  const pusher = getEcho().connector.pusher
  pusher.disconnect()
  pusher.connect()
}

function subscribe(onChange: () => void): () => void {
  return getEcho().connector.onConnectionChange(() => onChange())
}

function snapshot(): LiveStatus {
  return getEcho().connectionStatus()
}

/** The socket's state, re-rendering on every change. Creates the client on first use. */
export function useLiveStatus(): LiveStatus {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}
