// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FakeSocket } from '../test-support/fake-socket'
import { APP_ID, EXCHANGE_PATH, addressOf, boot, exchange, readPlace, start, type PageEnv } from './main'

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

const STARTED = {
  enabled: true, reason: null, device: null, stream: { encoding: 'auto', fps: 30 }, cursor: { enabled: true, lead_ms: 250 },
  connector: 'idb', capabilities: [], fallback_reason: null, ticket: 'tk',
}

function reply(status: number, body: unknown): Response {
  return { ok: status < 400, status, json: async () => body } as Response
}

/** A server that spends `code` for a viewer token and starts any scope. */
function server(code: string) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url !== EXCHANGE_PATH) return reply(200, { ok: true, data: STARTED })
    const spent = JSON.parse(String(init?.body)).code === code
    return spent ? reply(200, { ok: true, data: { token: 'viewer-token' } }) : reply(401, { detail: 'invalid or expired code' })
  })
}

function env(pathname: string, hash: string, fetcher: ReturnType<typeof vi.fn>, search = '') {
  const replaceState = vi.fn()
  const titles: string[] = []
  const page: PageEnv = {
    location: { pathname, search, hash },
    history: { replaceState },
    fetch: fetcher as unknown as typeof fetch,
    title: (text) => {
      titles.push(text)
    },
  }
  return { page, replaceState, titles }
}

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as never
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('readPlace', () => {
  it('reads which page, which scope, and the code or ticket that lets it in', () => {
    expect(readPlace({ pathname: '/viewer/project-notes-1a2b', hash: '#code=c0de' }))
      .toEqual({ page: 'viewer', scope: 'project-notes-1a2b', code: 'c0de', device: null })
    expect(readPlace({ pathname: '/embed/demo/', hash: '#ticket=t1&code=ignored' }))
      .toEqual({ page: 'embed', scope: 'demo', code: 't1', device: null })
    expect(readPlace({ pathname: '/viewer/ws%3Aa', hash: '' }))
      .toEqual({ page: 'viewer', scope: 'ws:a', code: null, device: null })
    expect(readPlace({ pathname: '/healthz', hash: '#code=x' })).toBeNull()
    expect(readPlace({ pathname: '/viewer/a/b', hash: '' })).toBeNull()
  })

  it('reads the device an address shows instead of the scope’s own, and writes it back the same way', () => {
    const udid = '00000000-0000-0000-0000-000000000001'
    expect(readPlace({ pathname: '/viewer/demo', search: `?device=${udid}`, hash: '#code=c0de' }))
      .toEqual({ page: 'viewer', scope: 'demo', code: 'c0de', device: udid })
    expect(readPlace({ pathname: '/viewer/demo', search: '?device=', hash: '' })?.device).toBeNull()
    expect(readPlace({ pathname: '/viewer/demo', search: '?device=a%20b', hash: '' })?.device).toBe('a b')
    expect(addressOf('/viewer/demo', 'a b')).toBe('/viewer/demo?device=a+b')
    expect(addressOf('/viewer/demo', null)).toBe('/viewer/demo')
  })
})

describe('exchange', () => {
  it('spends a code for a viewer token, or says why not', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(reply(200, { ok: true, data: { token: 'viewer-token' } }))
      .mockResolvedValueOnce(reply(200, { ok: true, data: { token: 'settings-token', kind: 'settings' } }))
      .mockResolvedValueOnce(reply(401, { detail: 'invalid or expired code' }))
      .mockResolvedValueOnce(reply(502, null))
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => { throw new SyntaxError('not JSON') } })
    expect(await exchange('c0de', fetcher)).toEqual({ token: 'viewer-token', kind: null })
    expect(await exchange('c0de', fetcher)).toEqual({ token: 'settings-token', kind: 'settings' })
    expect(fetcher).toHaveBeenCalledWith(EXCHANGE_PATH, expect.objectContaining({ method: 'POST', body: '{"code":"c0de"}' }))
    await expect(exchange('c0de', fetcher)).rejects.toThrow('invalid or expired code')
    await expect(exchange('c0de', fetcher)).rejects.toThrow('HTTP 502')
    await expect(exchange('c0de', fetcher)).rejects.toThrow('HTTP 200')
  })
})

describe('boot', () => {
  it('says what to do on an address that is no viewer, or one without its code', async () => {
    const root = document.createElement('div')
    expect(await boot(root, env('/elsewhere', '', vi.fn()).page)).toBeNull()
    expect(root.textContent).toBe('This is not a SimMirror viewer address.')
    expect(await boot(root, env('/viewer/demo', '', vi.fn()).page)).toBeNull()
    expect(root.textContent).toContain('sim-mirror open')
    expect(await boot(root, env('/embed/demo', '', vi.fn()).page)).toBeNull()
    expect(root.textContent).toContain('Reload the page that shows this frame.')
  })

  it('takes the code out of the address before spending it, and says why when it cannot be spent', async () => {
    const root = document.createElement('div')
    const { page, replaceState } = env('/viewer/demo', '#code=spent', server('c0de'))
    expect(await boot(root, page)).toBeNull()
    expect(replaceState).toHaveBeenCalledWith(null, '', '/viewer/demo')
    expect(root.textContent).toBe('invalid or expired code. Open the viewer again with `sim-mirror open`.')
  })

  it('opens the scope’s viewer with the token its ticket was spent for', async () => {
    const root = document.createElement('div')
    document.body.appendChild(root)
    const fetcher = server('t1')
    const { page, titles } = env('/embed/demo', '#ticket=t1', fetcher)
    const view = await boot(root, page)
    await flush()
    expect(view).not.toBeNull()
    expect(titles).toEqual(['demo · SimMirror'])
    expect(root.querySelector('.smv')).not.toBeNull()
    const [, init] = fetcher.mock.calls[1] as [string, RequestInit]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer viewer-token')
    expect(FakeSocket.last().url).toContain('/api/v1/scopes/demo/screen?ticket=tk')
    // A framed page offers no settings.
    expect(root.querySelector<HTMLButtonElement>('[data-smv="settings"]')!.hidden).toBe(true)
    view!.destroy()
  })

  it('offers the scope’s settings on its own page, as the session it was opened with', async () => {
    const root = document.createElement('div')
    document.body.appendChild(root)
    const fetcher = vi.fn(async (url: string) => (url === EXCHANGE_PATH
      ? reply(200, { ok: true, data: { token: 'settings-token', kind: 'settings' } })
      : reply(200, { ok: true, data: STARTED })))
    const view = await boot(root, env('/viewer/demo', '#code=c1', fetcher).page)
    await flush()
    const gear = root.querySelector<HTMLButtonElement>('[data-smv="settings"]')!
    expect(gear.hidden).toBe(false)
    gear.click()
    await flush()
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith('/api/v1/scopes/demo/settings'))).toBe(true)
    view!.destroy()
  })
})

describe('a tab of its own device', () => {
  const choice = (udid: string, name: string) => ({
    udid, name, runtime: 'iOS 26.5', state: 'Booted', created: false, kind: 'simulator', connection: null, detail: null,
    usable: true, agents: [],
  })
  const ready = (udid: string, name: string) => ({
    udid, name, runtime: 'iOS 26.5', state: 'ready', reason: null, since_ms: 0, viewers: 1, busy: null, created: false,
    booted_by_us: false, screen: null, app_hierarchy: null, kind: 'simulator', connection: null, buttons: [],
    recording: null, appearance: null,
  })

  it('shows the device its address names, keeps the address in step with the picker, and names it in its title', async () => {
    const root = document.createElement('div')
    document.body.appendChild(root)
    const asked: string[] = []
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === EXCHANGE_PATH) return reply(200, { ok: true, data: { token: 'viewer-token', kind: 'viewer' } })
      const path = new URL(url).pathname + new URL(url).search
      asked.push(`${init?.method} ${path}`)
      if (path.endsWith('/devices')) {
        return reply(200, { ok: true, data: { devices: [choice('U1', 'iPhone 17 Pro'), choice('U2', 'iPhone Air')] } })
      }
      const shown = path.includes('udid=U2') ? ready('U2', 'iPhone Air') : ready('U1', 'iPhone 17 Pro')
      return reply(200, { ok: true, data: { ...STARTED, device: shown, default_udid: 'U1' } })
    })
    const { page, replaceState, titles } = env('/viewer/demo', '#code=c0de', fetcher, '?device=U2')
    const view = await boot(root, page)
    await flush()
    expect(replaceState).toHaveBeenCalledWith(null, '', '/viewer/demo?device=U2')
    expect(asked).toContain('POST /api/v1/scopes/demo?udid=U2')
    expect(titles).toEqual(['demo · SimMirror', 'iPhone Air · demo · SimMirror'])
    FakeSocket.last().open()

    const picker = async () => {
      root.querySelector<HTMLButtonElement>('[data-smv="devices"]')!.click()
      await flush()
    }
    await picker()
    expect(root.querySelector('[data-smv-picker]')!.textContent).toContain('Follow the project')
    root.querySelector<HTMLButtonElement>('[data-smv="follow"]')!.click()
    await flush()
    expect(replaceState).toHaveBeenLastCalledWith(null, '', '/viewer/demo')
    expect(asked.at(-1)).toBe('POST /api/v1/scopes/demo')
    expect(titles.at(-1)).toBe('iPhone 17 Pro · demo · SimMirror')
    FakeSocket.last().open()

    await picker()
    root.querySelector<HTMLButtonElement>('[data-smv-udid="U2"]')!.click()
    await flush()
    expect(replaceState).toHaveBeenLastCalledWith(null, '', '/viewer/demo?device=U2')
    expect(asked.at(-1)).toBe('POST /api/v1/scopes/demo?udid=U2')
    // Nothing moved the scope's own device.
    expect(asked.filter((call) => call.startsWith('PUT'))).toEqual([])
    view!.destroy()
  })
})

describe('start', () => {
  it('boots into the page’s app element, and does nothing on a page without one', async () => {
    expect(await start()).toBeNull()
    const root = document.createElement('main')
    root.id = APP_ID
    document.body.appendChild(root)
    vi.stubGlobal('fetch', server('c0de'))
    window.history.replaceState(null, '', '/viewer/demo#code=wrong')
    expect(await start(document, window)).toBeNull()
    expect(root.textContent).toContain('invalid or expired code')
    expect(window.location.hash).toBe('')
    window.history.replaceState(null, '', '/viewer/demo#code=c0de')
    const view = await start()
    expect(view).not.toBeNull()
    expect(document.title).toBe('demo · SimMirror')
    view!.destroy()
  })
})
