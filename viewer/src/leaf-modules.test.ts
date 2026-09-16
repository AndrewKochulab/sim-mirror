// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from 'vitest'
import { escapeHTML } from './escape'
import { createHttpTransport, TransportError } from './http-transport'
import { ICON_NAMES, lucideSvg } from './icons'

describe('escapeHTML', () => {
  it('turns what markup is made of into entities, and nothing into nothing', () => {
    expect(escapeHTML(`<a href="x" title='y'>&</a>`)).toBe('&lt;a href=&quot;x&quot; title=&#39;y&#39;&gt;&amp;&lt;/a&gt;')
    expect(escapeHTML(null)).toBe('')
    expect(escapeHTML(undefined)).toBe('')
    expect(escapeHTML('')).toBe('')
  })
})

describe('icons', () => {
  it('draws every icon as Lucide does, inline, named for what it is', () => {
    for (const name of ICON_NAMES) {
      const holder = document.createElement('div')
      holder.innerHTML = lucideSvg(name)
      const svg = holder.querySelector('svg')!
      expect(svg.getAttribute('data-icon')).toBe(name)
      expect(svg.getAttribute('viewBox')).toBe('0 0 24 24')
      expect(svg.getAttribute('stroke')).toBe('currentColor')
      expect(svg.children.length).toBeGreaterThan(0)
    }
    expect(lucideSvg('lock')).toContain('<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/>')
    expect(ICON_NAMES).toContain('pointer')
  })
})

function answering(...answers: Array<{ status?: number; body?: unknown; broken?: boolean }>) {
  const calls: Array<{ url: string; init: RequestInit }> = []
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init })
    const answer = answers.shift() ?? { body: { ok: true, data: null } }
    return {
      ok: (answer.status ?? 200) < 400,
      status: answer.status ?? 200,
      json: async () => {
        if (answer.broken) throw new SyntaxError('not JSON')
        return answer.body
      },
    } as Response
  })
  return { calls, fetcher: fetcher as unknown as typeof fetch }
}

describe('createHttpTransport', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reaches a scope’s routes on the server, with the token asked for on every request', async () => {
    let token: string | null = 'first'
    const { calls, fetcher } = answering(
      { body: { ok: true, data: { enabled: true } } },
      { body: { ok: true, data: { ticket: 'tk' } } },
      { body: { ok: true, data: { stopped: true } } },
      { body: { ok: true, data: { devices: [{ udid: 'U1' }] } } },
      { body: { ok: true, data: { udid: 'U1' } } },
      { body: { ok: true, data: { stopped: false } } },
    )
    const transport = createHttpTransport({
      baseUrl: 'http://127.0.0.1:7466', scope: 'project notes', fetch: fetcher, token: async () => token,
    })
    expect(await transport.status()).toEqual({ enabled: true })
    token = null
    expect(await transport.start()).toEqual({ ticket: 'tk' })
    expect(await transport.stop(true)).toBe(true)
    expect(await transport.devices()).toEqual([{ udid: 'U1' }])
    await transport.choose('U1')
    expect(await transport.stop(false)).toBe(false)
    expect(calls.map((call) => `${call.init.method} ${call.url}`)).toEqual([
      'GET http://127.0.0.1:7466/api/v1/scopes/project%20notes',
      'POST http://127.0.0.1:7466/api/v1/scopes/project%20notes',
      'DELETE http://127.0.0.1:7466/api/v1/scopes/project%20notes?shutdown=true',
      'GET http://127.0.0.1:7466/api/v1/scopes/project%20notes/devices',
      'PUT http://127.0.0.1:7466/api/v1/scopes/project%20notes/device',
      'DELETE http://127.0.0.1:7466/api/v1/scopes/project%20notes',
    ])
    expect(calls[0].init.headers).toEqual({ Accept: 'application/json', Authorization: 'Bearer first' })
    expect(calls[1].init.headers).toEqual({ Accept: 'application/json', 'Content-Type': 'application/json' })
    expect(calls[4].init.body).toBe('{"udid":"U1"}')
    expect(calls[0].init.credentials).toBe('same-origin')
  })

  it('rejects with what the server said, or its status when it said nothing readable', async () => {
    const { fetcher } = answering(
      { status: 409, body: { detail: 'The iOS Simulator is off for this project.' } },
      { status: 403, body: { ok: false, error: 'cross-origin request refused' } },
      { status: 502, broken: true },
      { status: 500, body: { detail: '' } },
    )
    const transport = createHttpTransport({ baseUrl: 'http://127.0.0.1:7466', scope: 'demo', fetch: fetcher })
    const off = { detail: 'The iOS Simulator is off for this project.' }
    await expect(transport.start()).rejects.toEqual(new TransportError(off.detail, 409, off))
    await expect(transport.status()).rejects.toThrow('cross-origin request refused')
    const broken = await transport.devices().catch((error: TransportError) => error)
    expect(broken).toBeInstanceOf(TransportError)
    expect([(broken as TransportError).message, (broken as TransportError).status]).toEqual(['HTTP 502', 502])
    await expect(transport.status()).rejects.toThrow('HTTP 500')
  })

  it('reads and changes settings only when told the server serves them, and keeps a refusal\'s body', async () => {
    const without = createHttpTransport({ baseUrl: 'http://127.0.0.1:7466', scope: 'demo', fetch: vi.fn() })
    expect(without.settings).toBeUndefined()
    expect(without.changeSettings).toBeUndefined()
    const refusal = { detail: 'Changing build.tools needs a person at the terminal', errors: [], confirmation: { id: 'c1' } }
    const { fetcher, calls } = answering(
      { body: { ok: true, data: { scope: 'demo', settings: [] } } },
      { status: 428, body: refusal },
    )
    const transport = createHttpTransport({ baseUrl: 'http://127.0.0.1:7466', scope: 'demo', fetch: fetcher, settings: true })
    expect(await transport.settings!()).toEqual({ scope: 'demo', settings: [] })
    const change = { target: 'scope' as const, set: [{ path: 'build.tools', value: true }], unset: [], confirmation: null }
    const refused = await transport.changeSettings!(change).catch((error: TransportError) => error)
    expect([(refused as TransportError).status, (refused as TransportError).body]).toEqual([428, refusal])
    expect(calls.map((call) => `${call.init.method} ${call.url}`)).toEqual([
      'GET http://127.0.0.1:7466/api/v1/scopes/demo/settings',
      'PATCH http://127.0.0.1:7466/api/v1/scopes/demo/settings',
    ])
    expect(calls[1].init.body).toBe(JSON.stringify(change))
  })

  it('opens the screen socket on the same server, secure when the page is', () => {
    const plain = createHttpTransport({ baseUrl: 'http://127.0.0.1:7466', scope: 'demo', fetch: vi.fn() })
    expect(plain.socketUrl('t k')).toBe('ws://127.0.0.1:7466/api/v1/scopes/demo/screen?ticket=t+k')
    const secure = createHttpTransport({ scope: 'demo', origin: 'https://host.example', prefix: '/sim', fetch: vi.fn() })
    expect(secure.socketUrl('tk')).toBe('wss://host.example/sim/demo/screen?ticket=tk')
  })

  it('uses the page’s own origin and fetch when given neither', async () => {
    const fetcher = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ ok: true, data: { enabled: false } }) }))
    vi.stubGlobal('fetch', fetcher)
    const transport = createHttpTransport({ scope: 'demo' })
    expect(await transport.status()).toEqual({ enabled: false })
    expect(fetcher).toHaveBeenCalledWith(`${window.location.origin}/api/v1/scopes/demo`, expect.anything())
    expect(transport.socketUrl('tk').startsWith('ws://')).toBe(true)
  })
})
