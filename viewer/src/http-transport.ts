// SPDX-License-Identifier: Apache-2.0
/**
 * The viewer's transport over a SimMirror server's HTTP routes: ``/api/v1/scopes/<scope>`` and its screen socket.
 *
 * A token, when there is one, goes in an ``Authorization`` header, asked for on every request so a page can hold it in
 * memory and replace it. A refusal rejects with what the server said -- its ``detail``, or a security refusal's
 * ``error`` -- so the viewer can show it.
 */
import type { DeviceChoice, ScopeStatus, SettingsChange, SettingsView, Started } from './protocol.generated'
import type { SimMirrorTransport } from './transport'

export const SCOPES_PREFIX = '/api/v1/scopes'

export interface HttpTransportOptions {
  /** The server, such as `http://127.0.0.1:7466`; the page's own origin when empty. */
  baseUrl?: string
  scope: string
  /** The bearer token for each request, or none. */
  token?: () => string | null | Promise<string | null>
  /** Where the scope routes are mounted; `/api/v1/scopes` on the daemon. */
  prefix?: string
  fetch?: typeof fetch
  /** The page's origin, for a relative `baseUrl`; `window.location` when not given. */
  origin?: string
  /** Offer the scope's settings, where the server mounts its settings routes; false when absent. */
  settings?: boolean
}

export class TransportError extends Error {
  /** What the server answered with, for a caller that reads more than the message. */
  constructor(message: string, readonly status: number, readonly body: unknown = null) {
    super(message)
    this.name = 'TransportError'
  }
}

function said(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null
  const body = payload as Record<string, unknown>
  const text = body.detail ?? body.error
  return typeof text === 'string' && text ? text : null
}

export function createHttpTransport(options: HttpTransportOptions): SimMirrorTransport {
  const request = options.fetch ?? ((input: RequestInfo | URL, init?: RequestInit) => fetch(input, init))
  const origin = options.baseUrl || options.origin || window.location.origin
  const base = `${options.prefix ?? SCOPES_PREFIX}/${encodeURIComponent(options.scope)}`

  async function call<T>(method: string, path = '', body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const token = options.token ? await options.token() : null
    if (token) headers.Authorization = `Bearer ${token}`
    const response = await request(new URL(base + path, origin).toString(), {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin',
    })
    const payload: unknown = await response.json().catch(() => null)
    if (!response.ok) throw new TransportError(said(payload) ?? `HTTP ${response.status}`, response.status, payload)
    return (payload as { data: T }).data
  }

  const settings: Pick<SimMirrorTransport, 'settings' | 'changeSettings'> = options.settings
    ? {
      settings: () => call<SettingsView>('GET', '/settings'),
      changeSettings: (change: SettingsChange) => call<SettingsView>('PATCH', '/settings', change),
    }
    : {}

  return {
    ...settings,
    status: () => call<ScopeStatus>('GET'),
    start: () => call<Started>('POST', '', {}),
    stop: async (shutdown) => (await call<{ stopped: boolean }>('DELETE', shutdown ? '?shutdown=true' : '')).stopped,
    devices: async () => (await call<{ devices: DeviceChoice[] }>('GET', '/devices')).devices,
    choose: async (udid) => {
      await call('PUT', '/device', { udid })
    },
    socketUrl(ticket) {
      const url = new URL(`${base}/screen`, origin)
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
      url.searchParams.set('ticket', ticket)
      return url.toString()
    },
  }
}
