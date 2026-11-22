// SPDX-License-Identifier: Apache-2.0
/**
 * The daemon's own viewer pages: ``/viewer/<scope>#code=…`` for a browser tab (``sim-mirror open``) and
 * ``/embed/<scope>#ticket=…`` for a frame a host's page shows.
 *
 * The code or ticket sits in the URL's fragment, which a browser never sends to a server. It is spent once: it leaves
 * the address bar and the history first, then goes to ``/api/v1/auth/exchange`` for a viewer token for that scope,
 * kept in this page's memory and nowhere else.
 *
 * A viewer page offers the scope's settings -- to change them when the code was minted with ``--settings``, and to read
 * them otherwise; a framed page does not.
 *
 * A page shows the scope's own device, and follows it when a person or an agent picks another -- unless its address
 * names one of the scope's devices, ``?device=<udid>`` (``sim-mirror open --device``): then it shows that one, and comes
 * back to it after a restart, whichever the scope's own is. Its picker's Show in this tab and Follow the project keep
 * the address in step, without reloading, and its title names the device it shows.
 */
import { createHttpTransport, TransportError } from '../src/http-transport'
import { createViewer, type ViewHandle } from '../src/viewer'

export const APP_ID = 'sim-mirror-app'
export const EXCHANGE_PATH = '/api/v1/auth/exchange'

export interface Place {
  page: 'viewer' | 'embed'
  scope: string
  code: string | null
  /** The one of the scope's devices the page shows, by UDID, instead of its own; null to follow its own. */
  device: string | null
}

export interface PageEnv {
  location: { pathname: string; search?: string; hash: string }
  history: { replaceState(data: unknown, unused: string, url?: string): void }
  fetch: typeof fetch
  title(text: string): void
}

/** Which page this is, for which scope, the code or ticket that lets it in, and the device it shows. */
export function readPlace(location: { pathname: string; search?: string; hash: string }): Place | null {
  const match = /^\/(viewer|embed)\/([^/]+)\/?$/.exec(location.pathname)
  if (!match) return null
  const page = match[1] as Place['page']
  const fragment = new URLSearchParams(location.hash.replace(/^#/, ''))
  const device = new URLSearchParams(location.search ?? '').get('device') || null
  return { page, scope: decodeURIComponent(match[2]), code: fragment.get(page === 'embed' ? 'ticket' : 'code'), device }
}

/** The page's address with no code in it: its path, and the device it shows when it shows one of its own. */
export function addressOf(pathname: string, device: string | null): string {
  return device ? `${pathname}?${new URLSearchParams({ device }).toString()}` : pathname
}

export interface Session {
  token: string
  /** What the session may do: `viewer`, `embed` or `settings`; null from a daemon too old to say, which serves no settings. */
  kind: string | null
}

/** Spend a code or ticket for a viewer token. Rejects with what the server said. */
export async function exchange(code: string, request: typeof fetch): Promise<Session> {
  const response = await request(EXCHANGE_PATH, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
    credentials: 'same-origin',
  })
  const payload = (await response.json().catch(() => null)) as {
    data?: { token?: unknown; kind?: unknown }; detail?: unknown
  } | null
  const token = payload?.data?.token
  const kind = payload?.data?.kind
  if (response.ok && typeof token === 'string') return { token, kind: typeof kind === 'string' ? kind : null }
  throw new TransportError(typeof payload?.detail === 'string' ? payload.detail : `HTTP ${response.status}`, response.status)
}

function notice(root: HTMLElement, text: string): null {
  const note = document.createElement('p')
  note.className = 'smv-page-note'
  note.textContent = text
  root.replaceChildren(note)
  return null
}

export async function boot(root: HTMLElement, env: PageEnv): Promise<ViewHandle | null> {
  const place = readPlace(env.location)
  if (!place) return notice(root, 'This is not a SimMirror viewer address.')
  const again = place.page === 'viewer' ? 'Open the viewer again with `sim-mirror open`.'
    : 'Reload the page that shows this frame.'
  if (!place.code) return notice(root, `This page lets you in with a one-time link. ${again}`)
  const pathname = env.location.pathname
  env.history.replaceState(null, '', addressOf(pathname, place.device))
  let session: Session
  try {
    session = await exchange(place.code, env.fetch)
  } catch (error) {
    return notice(root, `${(error as Error).message}. ${again}`)
  }
  let titled = `${place.scope} · SimMirror`
  env.title(titled)
  const view = createViewer(root, {
    transport: createHttpTransport({
      scope: place.scope, token: () => session.token, fetch: env.fetch,
      settings: session.kind === 'viewer' || session.kind === 'settings', device: place.device,
    }),
    placement: 'page',
    device: place.device,
    // A tab says which device it shows, so two tabs side by side are told apart.
    onState: ({ device }) => {
      const next = device ? `${device.name} · ${place.scope} · SimMirror` : titled
      if (next !== titled) env.title((titled = next))
    },
    onDevice: (udid) => env.history.replaceState(null, '', addressOf(pathname, udid)),
  })
  view.setActive(true)
  return view
}

/** Boot the page into its app element, when it has one. */
export function start(doc: Document = document, win: Window = window): Promise<ViewHandle | null> {
  const root = doc.getElementById(APP_ID)
  if (!root) return Promise.resolve(null)
  return boot(root, {
    location: win.location,
    history: win.history,
    fetch: (input, init) => win.fetch(input, init),
    title: (text) => {
      doc.title = text
    },
  })
}
