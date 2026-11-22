// SPDX-License-Identifier: Apache-2.0
/** A view of its own device: the picker's Show in this tab, Use for the project and Follow the project, and a view that
 * comes back to its own device after a restart. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FakeSocket } from '../test-support/fake-socket'
import { CLOCK, DEVICE, flush, hear, live, setup, socket, started } from '../test-support/viewer-harness'
import { createHttpTransport } from './http-transport'
import { CLOSE_RESTARTING, type DeviceChoice } from './protocol.generated'
import { createViewer } from './viewer'
import { pickerMarkup } from './viewer-controls'
import { RECONNECT_MS } from './viewer-stream'

const SECOND = { ...DEVICE, udid: 'U2', name: 'iPhone Air' }

/** A transport that can show any of the scope's devices, whose own device is `own`. */
function tabs(own: string | null = 'U1', shown = DEVICE) {
  return {
    use: vi.fn(),
    status: vi.fn(async () => ({ ...started(), default_udid: own })),
    start: vi.fn(async () => started(shown)),
  }
}

const open = async ($: (selector: string) => Element) => {
  ($('[data-smv="devices"]') as HTMLButtonElement).click()
  await flush()
}

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('requestAnimationFrame', (paint: FrameRequestCallback) => { paint(0); return 1 })
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as never
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('the picker of a view that can show any device', () => {
  it('shows a picked device in this view alone, and never moves the scope’s own device', async () => {
    const onDevice = vi.fn()
    const own = tabs()
    const { view, transport, $ } = await live({ transport: own, onDevice })
    const first = socket()
    await open($)
    const picker = $('[data-smv-picker]')
    expect(picker.querySelector('.smv-picker-group')!.textContent).toBe('Show in this tab')
    expect(picker.querySelector('[data-smv="use-for-project"]')).toBeNull()
    expect(picker.querySelector('[data-smv="follow"]')).toBeNull()
    view.el.querySelector<HTMLButtonElement>('[data-smv-udid="U2"]')!.click()
    await flush()
    expect(own.use).toHaveBeenCalledWith('U2')
    expect(transport.choose).not.toHaveBeenCalled()
    expect(onDevice).toHaveBeenCalledWith('U2')
    expect(first.closed).toBe(true)
    expect(transport.start).toHaveBeenCalledTimes(2)
  })

  it('keeps the device it shows when that one is picked, without reconnecting', async () => {
    const onDevice = vi.fn()
    const own = tabs()
    const { view, transport, $ } = await live({ transport: own, onDevice })
    await open($)
    view.el.querySelector<HTMLButtonElement>('[data-smv-udid="U1"]')!.click()
    await flush()
    expect([own.use.mock.calls, onDevice.mock.calls]).toEqual([[['U1']], [['U1']]])
    expect(transport.start).toHaveBeenCalledOnce()
    // Picked again, it is what the view already shows and keeps.
    await open($)
    view.el.querySelector<HTMLButtonElement>('[data-smv-udid="U1"]')!.click()
    await flush()
    expect(own.use).toHaveBeenCalledOnce()
    expect($('[data-smv-picker]').innerHTML).toBe('')
  })

  it('in a view of its own device, uses that device for the project, or follows the project again', async () => {
    const onDevice = vi.fn()
    const transport = tabs('U1', SECOND)
    const { view, $, transport: merged } = setup({ transport, device: 'U2', onDevice })
    expect(transport.use).toHaveBeenCalledWith('U2')
    view.setActive(true)
    await flush()
    hear(undefined, SECOND)
    await open($)
    const picker = $('[data-smv-picker]')
    expect(picker.querySelector('[aria-checked="true"]')!.getAttribute('data-smv-udid')).toBe('U2')
    expect(picker.querySelector('[data-smv="follow"]')!.textContent).toBe('Follow the project')
    picker.querySelector<HTMLButtonElement>('[data-smv="use-for-project"]')!.click()
    await flush()
    expect(merged.choose).toHaveBeenCalledWith('U2')
    // The device it shows is the project's own now: following the project, it stays on its screen.
    expect(transport.use).toHaveBeenLastCalledWith(null)
    expect(onDevice).toHaveBeenLastCalledWith(null)
    expect(transport.start).toHaveBeenCalledOnce()
  })

  it('follows the project again on a person’s request, reconnecting to the scope’s own device', async () => {
    const onDevice = vi.fn()
    const transport = tabs('U1', SECOND)
    const { view, $ } = setup({ transport, device: 'U2', onDevice })
    view.setActive(true)
    await flush()
    hear(undefined, SECOND)
    const first = socket()
    await open($)
    $('[data-smv-picker]').querySelector<HTMLButtonElement>('[data-smv="follow"]')!.click()
    await flush()
    expect([transport.use.mock.lastCall, onDevice.mock.lastCall]).toEqual([[null], [null]])
    expect(first.closed).toBe(true)
    expect(transport.start).toHaveBeenCalledTimes(2)
  })

  it('offers to use the device it shows for the project when the server does not say which is the project’s', async () => {
    const choose = vi.fn().mockRejectedValueOnce(new Error('That simulator does not exist on this Mac.'))
      .mockRejectedValueOnce(new Error(''))
    const status = vi.fn().mockRejectedValueOnce(new Error('gone')).mockResolvedValue(started())
    const own = { ...tabs(), status, choose }
    const { view, $ } = await live({ transport: own })
    for (const said of ['That simulator does not exist on this Mac.', 'That device could not be used.']) {
      await open($)
      $('[data-smv-picker]').querySelector<HTMLButtonElement>('[data-smv="use-for-project"]')!.click()
      await flush()
      expect($('[data-smv-note]').textContent).toContain(said)
    }
    expect(choose).toHaveBeenCalledWith('U1')
    choose.mockResolvedValueOnce(undefined)
    await open($)
    $('[data-smv-picker]').querySelector<HTMLButtonElement>('[data-smv="use-for-project"]')!.click()
    await flush()
    expect(choose).toHaveBeenCalledTimes(3)
    // A view following the project has nothing to follow again, and is told nothing.
    expect(own.use).not.toHaveBeenCalled()
    view.destroy()
  })

  it('reconnects when following the project and the server does not say which device is the project’s', async () => {
    const transport = { ...tabs('U1', SECOND), status: vi.fn(async () => { throw new Error('gone') }) }
    const { view, $ } = setup({ transport, device: 'U2' })
    view.setActive(true)
    await flush()
    hear(undefined, SECOND)
    await open($)
    $('[data-smv-picker]').querySelector<HTMLButtonElement>('[data-smv="follow"]')!.click()
    await flush()
    expect(transport.start).toHaveBeenCalledTimes(2)
  })

  it('picks the scope’s own device through a transport that cannot show another, as it always did', async () => {
    const { view, transport, $ } = await live({ device: 'U2' })
    await open($)
    expect($('[data-smv-picker]').querySelector('.smv-picker-group')).toBeNull()
    view.el.querySelector<HTMLButtonElement>('[data-smv-udid="U2"]')!.click()
    await flush()
    expect(transport.choose).toHaveBeenCalledWith('U2')
    expect(transport.status).not.toHaveBeenCalled()
  })

  it('says which agents work on each device it lists', () => {
    const choice = (udid: string, agents?: DeviceChoice['agents']): DeviceChoice => ({
      udid, name: udid, runtime: 'iOS 26.5', state: 'Booted', created: false, kind: 'simulator', connection: null,
      detail: null, usable: true, ...(agents ? { agents } : {}),
    }) as DeviceChoice
    const markup = pickerMarkup([
      choice('U1', [{ key: 'a', title: 'Codex' }, { key: 'b', title: 'Claude <Code>' }]), choice('U2', []), choice('U3'),
    ], DEVICE, { pinned: null, own: 'U1' })
    const rows = new DOMParser().parseFromString(markup, 'text/html').querySelectorAll('[data-smv-udid]')
    expect([...rows].map((row) => row.querySelector('span')!.textContent)).toEqual([
      'iOS 26.5 · used by Codex, Claude <Code>', 'iOS 26.5', 'iOS 26.5',
    ])
    expect(pickerMarkup([], DEVICE, { pinned: 'U1', own: 'U1' }))
      .toBe('<p class="smv-picker-note">No simulators or devices on this Mac.</p>'
        + '<button type="button" role="menuitem" data-smv="follow">Follow the project</button>'
        + '<button type="button" role="menuitem" data-smv="shutdown">Shut down this device</button>')
  })
})

describe('a view of its own device over HTTP', () => {
  it('starts on the device it was told to show, and comes back to it after a restart', async () => {
    vi.useFakeTimers(CLOCK)
    const urls: string[] = []
    const fetcher = vi.fn(async (url: string, init: RequestInit) => {
      urls.push(`${init.method} ${url}`)
      return { ok: true, status: 200, json: async () => ({ ok: true, data: started(SECOND) }) } as Response
    }) as unknown as typeof fetch
    const host = document.createElement('div')
    document.body.appendChild(host)
    const transport = createHttpTransport({ baseUrl: 'http://127.0.0.1:7466', scope: 'demo', fetch: fetcher })
    const view = createViewer(host, { transport, device: 'U2', canDecodeH264: () => false })
    view.setActive(true)
    await flush()
    hear(undefined, SECOND)
    socket().end(CLOSE_RESTARTING, 'the simulator is restarting')
    await vi.advanceTimersByTimeAsync(RECONNECT_MS[0])
    await flush()
    expect(urls).toEqual([
      'POST http://127.0.0.1:7466/api/v1/scopes/demo?udid=U2', 'POST http://127.0.0.1:7466/api/v1/scopes/demo?udid=U2',
    ])
    expect(FakeSocket.instances).toHaveLength(2)
    view.destroy()
  })
})
