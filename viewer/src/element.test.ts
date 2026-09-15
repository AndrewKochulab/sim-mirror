// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FakeSocket } from '../test-support/fake-socket'
import { defineSimMirrorElement, ELEMENT_NAME, SimMirrorElement } from './element'
import type { Started } from './protocol.generated'
import { STYLE_ID } from './styles'
import type { SimMirrorTransport } from './transport'

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

const STARTED: Started = {
  enabled: true, reason: null, device: null, stream: { encoding: 'auto', fps: 30 }, cursor: { enabled: true, lead_ms: 250 },
  connector: 'idb', capabilities: [], fallback_reason: null, ticket: 'tk',
}

function transport() {
  return {
    status: vi.fn(async () => STARTED),
    start: vi.fn(async () => STARTED),
    stop: vi.fn(async () => true),
    devices: vi.fn(async () => []),
    choose: vi.fn(async () => undefined),
    socketUrl: vi.fn((ticket: string) => `ws://host/screen?ticket=${ticket}`),
  }
}

const answering = () => vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ ok: true, data: STARTED }) }))

function element(attributes: Record<string, string> = {}): SimMirrorElement {
  const el = document.createElement(ELEMENT_NAME) as SimMirrorElement
  for (const [name, value] of Object.entries(attributes)) el.setAttribute(name, value)
  return el
}

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as never
  defineSimMirrorElement()
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('<sim-mirror>', () => {
  it('is defined once under its name, and a name already taken is left alone', () => {
    const registry = { get: vi.fn(() => undefined), define: vi.fn() }
    defineSimMirrorElement('sim-mirror-other', registry as unknown as CustomElementRegistry)
    expect(registry.define).toHaveBeenCalledOnce()
    const taken = { get: vi.fn(() => SimMirrorElement), define: vi.fn() }
    defineSimMirrorElement(ELEMENT_NAME, taken as unknown as CustomElementRegistry)
    expect(taken.define).not.toHaveBeenCalled()
    expect(customElements.get(ELEMENT_NAME)).toBeDefined()
  })

  it('shows its scope from its attributes, in a shadow root with its own styles, let in with its token', async () => {
    const fetcher = answering()
    vi.stubGlobal('fetch', fetcher)
    const el = element({ server: 'http://127.0.0.1:7466', scope: 'demo', token: 'viewer-token' })
    document.body.appendChild(el)
    await flush()
    const shadow = el.shadowRoot!
    expect(shadow.querySelector('.smv')).not.toBeNull()
    expect(shadow.querySelector(`style#${STYLE_ID}`)).not.toBeNull()
    expect(shadow.querySelector('[part="screen"]')).not.toBeNull()
    expect(fetcher).toHaveBeenCalledWith('http://127.0.0.1:7466/api/v1/scopes/demo', expect.objectContaining({
      method: 'POST', headers: expect.objectContaining({ Authorization: 'Bearer viewer-token' }),
    }))
    expect(FakeSocket.last().url).toBe('ws://127.0.0.1:7466/api/v1/scopes/demo/screen?ticket=tk')
    el.remove()
    expect(FakeSocket.last().closed).toBe(true)
    expect(el.view).toBeNull()
  })

  it('reaches its own page’s server when it names none', async () => {
    const fetcher = answering()
    vi.stubGlobal('fetch', fetcher)
    document.body.appendChild(element({ scope: 'demo' }))
    await flush()
    expect(fetcher).toHaveBeenCalledWith(`${window.location.origin}/api/v1/scopes/demo`, expect.anything())
  })

  it('tells the page how the device stands, and when a person places or closes it', async () => {
    const el = element({ placement: 'dock' })
    el.transport = transport() as unknown as SimMirrorTransport
    const heard: Array<[string, unknown]> = []
    for (const type of ['state', 'place', 'close']) {
      el.addEventListener(`sim-mirror:${type}`, (event) => heard.push([type, (event as CustomEvent).detail]))
    }
    document.body.appendChild(el)
    await flush()
    FakeSocket.last().open()
    expect(heard.at(-1)?.[0]).toBe('state')
    const shadow = el.shadowRoot!
    shadow.querySelector<HTMLButtonElement>('[data-smv="place"]')!.click()
    shadow.querySelector<HTMLButtonElement>('[data-smv="close"]')!.click()
    expect(heard.filter(([type]) => type !== 'state')).toEqual([['place', { placement: 'window' }], ['close', null]])
    el.setAttribute('placement', 'window')
    expect(el.view!.el.dataset.placement).toBe('window')
    el.setAttribute('placement', 'window')
    el.setAttribute('placement', 'nonsense')
    expect(el.view!.el.dataset.placement).toBe('page')
  })

  it('shows nothing without a scope or a transport, and starts over when either changes', async () => {
    const el = element()
    document.body.appendChild(el)
    expect(el.view).toBeNull()
    expect(el.shadowRoot!.children).toHaveLength(0)
    const first = transport()
    el.transport = first as unknown as SimMirrorTransport
    await flush()
    expect(el.view).not.toBeNull()
    expect(first.start).toHaveBeenCalledOnce()
    const second = transport()
    el.transport = second as unknown as SimMirrorTransport
    await flush()
    expect(second.start).toHaveBeenCalledOnce()
    expect(el.transport).toBe(second)
    el.setAttribute('scope', 'other')
    await flush()
    expect(second.start).toHaveBeenCalledTimes(2)
    el.remove()
    el.setAttribute('scope', 'again')
    el.transport = null
    expect(el.view).toBeNull()
  })
})
