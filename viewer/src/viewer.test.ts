// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const decoding = vi.hoisted(() => ({
  sinks: [] as Array<{
    options: { draw: (frame: { displayWidth: number; displayHeight: number }) => void; onError: (error: Error) => void }
    push: ReturnType<typeof vi.fn>
    close: ReturnType<typeof vi.fn>
  }>,
}))
vi.mock('./h264-decoder', () => ({
  createH264Sink: vi.fn((options: (typeof decoding.sinks)[number]['options']) => {
    const sink = { options, push: vi.fn(), close: vi.fn() }
    decoding.sinks.push(sink)
    return sink
  }),
}))

import { FakeSocket } from '../test-support/fake-socket'
import { ACTIVE_MS } from './agent-cursor'
import {
  CAPABILITIES, CLOSE_BAD_MESSAGE, CLOSE_FORBIDDEN, CLOSE_RESTARTING, CLOSE_STOPPED, CLOSE_UNSUPPORTED, TAG_JPEG,
  TEXT_MAX_CHARS, type Capability, type Device, type ServerHello, type Started,
} from './protocol.generated'
import { readDevice } from './status-view'
import { STYLE_ID } from './styles'
import type { SimMirrorTransport } from './transport'
import { createViewer, type ViewerOptions } from './viewer'
import { MOVE_MS, TYPE_SETTLE_MS } from './viewer-input'
import { RECONNECT_MS, readServerHello } from './viewer-stream'

/** Timers and the date are faked. */
const CLOCK = { toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] } as Parameters<
  typeof vi.useFakeTimers>[0]
/** The same with `performance.now`, which times moves and scrolls. */
const INPUT_CLOCK = {
  toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date', 'performance'],
} as Parameters<typeof vi.useFakeTimers>[0]

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

const FULL: Capability[] = CAPABILITIES.filter((capability) => capability !== 'build_preview')
const VIEW_ONLY: Capability[] = ['lifecycle', 'device_list', 'appearance', 'screenshot', 'stream_jpeg']
const HELLO: ServerHello = {
  type: 'hello', v: 1, server: 'sim-mirror/0.1.0', encodings: ['h264', 'jpeg'], connector: 'idb', capabilities: FULL,
  fallback_reason: null,
}

const DEVICE: Device = {
  udid: 'U1', name: 'iPhone 17 Pro', runtime: 'iOS 26.5', state: 'ready', reason: null, since_ms: 0, viewers: 1,
  busy: null, created: true, booted_by_us: true,
  screen: { points: { w: 402, h: 874 }, pixels: { w: 1206, h: 2622 }, scale: 3 },
}

function started(device: Device | null = { ...DEVICE, state: 'booting', since_ms: 2000 }): Started {
  return {
    enabled: true, reason: null, device, stream: { encoding: 'auto', fps: 30 }, cursor: { enabled: true, lead_ms: 250 },
    connector: 'idb', capabilities: FULL, fallback_reason: null, ticket: 'tk',
  }
}

type Calls = Partial<Record<keyof SimMirrorTransport, unknown>>

function transportWith(overrides: Calls = {}) {
  const calls = {
    status: vi.fn(async () => started()),
    start: vi.fn(async () => started()),
    stop: vi.fn(async () => true),
    socketUrl: vi.fn((ticket: string) => `ws://x/screen?ticket=${ticket}`),
    devices: vi.fn(async () => [
      { udid: 'U1', name: 'iPhone 17 Pro', runtime: 'iOS 26.5', state: 'Booted', created: true },
      { udid: 'U2', name: 'iPhone <Air>', runtime: 'iOS 26.5', state: 'Shutdown', created: false },
    ]),
    choose: vi.fn(async () => undefined),
  }
  // Every override is a mock too: it keeps the mock's type, so a test can read its calls.
  return Object.assign(calls, overrides) as typeof calls
}

const RECT = { left: 0, top: 0, width: 402, height: 874, right: 402, bottom: 874, x: 0, y: 0, toJSON: () => ({}) }

type SetupOptions = Partial<Omit<ViewerOptions, 'transport'>> & { transport?: Calls }

function setup(overrides: SetupOptions = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const { transport: calls, ...rest } = overrides
  const transport = transportWith(calls)
  const onState = vi.fn()
  const options: ViewerOptions = {
    transport: transport as unknown as SimMirrorTransport,
    canDecodeH264: () => false,
    placement: 'dock',
    onPlace: vi.fn(),
    pageHref: '/viewer/tp-1',
    onClose: vi.fn(),
    reducedMotion: () => true,
    onState,
    ...rest,
  }
  const view = createViewer(host, options)
  const $ = <T extends Element>(selector: string) => view.el.querySelector<T>(selector)!
  const canvas = $<HTMLCanvasElement>('[data-smv-canvas]')
  canvas.width = 402
  canvas.height = 874
  canvas.getBoundingClientRect = () => RECT as DOMRect
  $<HTMLElement>('[data-smv-overlay]').getBoundingClientRect = () => RECT as DOMRect
  return { host, transport, options, view, $, canvas, onState }
}

const socket = () => FakeSocket.last()
const said = (view: { el: HTMLElement }) => view.el.querySelector<HTMLElement>('[data-smv-empty]')!.textContent

function pointer(type: string, x: number, y: number, init: { id?: number; button?: number } = {}) {
  const event = new MouseEvent(type, { clientX: x, clientY: y, button: init.button ?? 0, bubbles: true, cancelable: true })
  Object.defineProperty(event, 'pointerId', { value: init.id ?? 1 })
  return event
}

function frame(tag: number, size = 4): ArrayBuffer {
  const bytes = new Uint8Array(size)
  bytes[0] = tag
  return bytes.buffer
}

/** Open the socket, say hello as a server, start the stream, and say the device is ready. */
function hear(hello: ServerHello = HELLO, device: Device = DEVICE) {
  socket().open()
  socket().message(hello)
  socket().message({ type: 'stream', encoding: 'jpeg' })
  socket().message({ type: 'status', ...device })
}

async function live(overrides: SetupOptions = {}, hello: ServerHello = HELLO) {
  const rig = setup(overrides)
  rig.view.setActive(true)
  await flush()
  hear(hello)
  socket().sent = []
  return rig
}

let drawImage: ReturnType<typeof vi.fn>

beforeEach(() => {
  FakeSocket.reset()
  decoding.sinks.length = 0
  drawImage = vi.fn()
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('createImageBitmap', vi.fn(async (blob: Blob) => ({ width: 1206, height: 2622, size: blob.size,
                                                                     close: vi.fn() })))
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage })) as never
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('readers', () => {
  it('takes a device state it can show, and nothing else', () => {
    expect(readDevice({ type: 'status', ...DEVICE })).toMatchObject({ udid: 'U1', state: 'ready' })
    expect(readDevice({ udid: 'U1', name: 'x', state: 'melting' })).toBeNull()
    expect(readDevice({ udid: 1, name: 'x', state: 'ready' })).toBeNull()
    expect(readDevice({ udid: 'U1', state: 'ready' })).toBeNull()
  })

  it('takes a server hello this viewer speaks, and nothing else', () => {
    expect(readServerHello({ ...HELLO })).toEqual(HELLO)
    expect(readServerHello({ ...HELLO, server: 7, fallback_reason: 'idb is not installed' }))
      .toEqual({ ...HELLO, server: '', fallback_reason: 'idb is not installed' })
    const refused: Array<Record<string, unknown>> = [
      { ...HELLO, type: 'stream' }, { ...HELLO, v: 2 }, { ...HELLO, encodings: 'jpeg' }, { ...HELLO, capabilities: [1] },
      { ...HELLO, connector: null }, { ...HELLO, fallback_reason: 3 },
    ]
    for (const body of refused) expect(readServerHello(body)).toBeNull()
  })
})

describe('createViewer', () => {
  it('connects only while active, says hello, counts the boot, then shows the screen and draws its frames', async () => {
    vi.useFakeTimers(CLOCK)
    const { view, transport, $, onState } = setup()
    await flush()
    expect(transport.start).not.toHaveBeenCalled()
    view.setActive(true)
    view.setActive(true)
    await flush()
    expect(transport.start).toHaveBeenCalledOnce()
    expect(transport.socketUrl).toHaveBeenCalledWith('tk')
    expect(socket().binaryType).toBe('arraybuffer')
    expect($('[data-smv-name]').textContent).toBe('iPhone 17 Pro · iOS 26.5')
    expect($<HTMLElement>('[data-smv-state]').dataset.state).toBe('booting')
    expect($('[data-smv-state]').textContent).toBe('Starting')
    expect(said(view)).toBe('Starting iPhone 17 Pro… 2s')
    vi.advanceTimersByTime(1000)
    expect(said(view)).toBe('Starting iPhone 17 Pro… 3s')

    socket().open()
    expect(socket().sent).toEqual([{ type: 'hello', v: 1, encodings: ['jpeg'] }])
    expect(view.connected()).toBe(true)
    socket().message({ type: 'hello', v: 2 })
    expect(view.hello).toBeNull()
    socket().message(HELLO)
    socket().message({ type: 'stream', encoding: 'webp' })
    expect(view.encoding).toBeNull()
    socket().message({ type: 'stream', encoding: 'jpeg' })
    expect(view.encoding).toBe('jpeg')
    expect(view.hello).toEqual(HELLO)
    socket().message({ type: 'status', ...DEVICE })
    expect($<HTMLElement>('[data-smv-empty]').hidden).toBe(true)
    expect($<HTMLCanvasElement>('[data-smv-canvas]').hidden).toBe(false)
    expect($('[data-smv-state]').textContent).toBe('Ready')
    expect($<HTMLElement>('[data-smv-mode]').hidden).toBe(true)
    expect(onState).toHaveBeenLastCalledWith({
      device: expect.objectContaining({ state: 'ready' }), connector: 'idb', capabilities: FULL, viewOnly: false,
      connected: true,
    })
    vi.advanceTimersByTime(5000)
    expect($<HTMLElement>('[data-smv-empty]').hidden).toBe(true)
    expect(view.device?.udid).toBe('U1')

    socket().message(frame(TAG_JPEG, 5))
    socket().message(frame(TAG_JPEG, 1))
    socket().message(frame(0x09))
    socket().message('not json')
    socket().message('3')
    socket().message({ type: 'status', udid: 'U1' })
    socket().message({ type: 'agent', id: 'a0' })
    socket().message({ type: 'other' })
    await flush()
    expect(createImageBitmap).toHaveBeenCalledOnce()
    expect((vi.mocked(createImageBitmap).mock.calls[0][0] as Blob).size).toBe(4)
    expect(drawImage).toHaveBeenCalledOnce()

    view.setActive(false)
    expect(socket().closed).toBe(true)
    expect(view.connected()).toBe(false)
  })

  it('shows a device it was not told about as a plain simulator, and starts on a page when not placed', async () => {
    const { view, $ } = setup({ transport: { start: vi.fn(async () => started(null)) }, placement: undefined })
    view.setActive(true)
    await flush()
    expect($('[data-smv-name]').textContent).toBe('iOS Simulator')
    expect($('[data-smv-state]').textContent).toBe('Stopped')
    expect(view.el.dataset.placement).toBe('page')
  })

  it('says how a device stands when it is not ready, and offers a way on where there is one', async () => {
    const { view, $ } = await live()
    const tell = (device: Partial<Device>) => socket().message({ type: 'status', ...DEVICE, ...device })
    tell({ state: 'stalled', reason: 'The companion stopped' })
    expect(said(view)).toBe('The companion stopped. Reconnecting…')
    expect(view.el.querySelector('[data-smv="start"]')).toBeNull()
    tell({ state: 'stalled' })
    expect(said(view)).toBe('The simulator stopped answering. Reconnecting…')
    tell({ state: 'failed', reason: 'Boot timed out' })
    expect(said(view)).toContain('Boot timed out')
    expect($('[data-smv="start"]').textContent).toBe('Try again')
    tell({ state: 'failed' })
    expect(said(view)).toContain('The simulator could not start.')
    tell({ state: 'stopped' })
    expect($('[data-smv="start"]').textContent).toBe('Start the simulator')
    expect($<HTMLCanvasElement>('[data-smv-canvas]').hidden).toBe(true)
  })

  it('sends one finger as a touch stream, a move at most every MOVE_MS, and nothing from outside the screen', async () => {
    vi.useFakeTimers(INPUT_CLOCK)
    const { canvas } = await live()
    canvas.dispatchEvent(pointer('pointerdown', 201, 437))
    canvas.dispatchEvent(pointer('pointerdown', 10, 10, { id: 2 }))
    canvas.dispatchEvent(pointer('pointermove', 100, 100))
    canvas.dispatchEvent(pointer('pointermove', 100.5, 218.5))
    canvas.dispatchEvent(pointer('pointermove', 1, 1, { id: 2 }))
    expect(socket().sent).toHaveLength(2)
    vi.advanceTimersByTime(MOVE_MS)
    canvas.dispatchEvent(pointer('pointermove', 300, 300))
    canvas.dispatchEvent(pointer('pointerup', 402, 874, { id: 2 }))
    canvas.dispatchEvent(pointer('pointerup', 402, 874))
    vi.advanceTimersByTime(MOVE_MS)
    canvas.dispatchEvent(pointer('pointermove', 5, 5))
    canvas.dispatchEvent(pointer('pointerdown', 40.2, 87.4, { button: 2 }))
    canvas.dispatchEvent(pointer('pointerdown', 40.2, 87.4))
    canvas.dispatchEvent(pointer('pointercancel', 40.2, 87.4))
    expect(socket().sent).toEqual([
      { type: 'touch', phase: 'down', nx: 0.5, ny: 0.5 },
      // The first move goes at once; the next waits out MOVE_MS and goes as the latest.
      { type: 'touch', phase: 'move', nx: 0.2488, ny: 0.1144 },
      { type: 'touch', phase: 'move', nx: 0.25, ny: 0.25 },
      // A move still waiting when the finger lifts goes before the lift.
      { type: 'touch', phase: 'move', nx: 0.7463, ny: 0.3432 },
      { type: 'touch', phase: 'up', nx: 1, ny: 1 },
      { type: 'touch', phase: 'down', nx: 0.1, ny: 0.1 },
      { type: 'touch', phase: 'cancel', nx: 0.1, ny: 0.1 },
    ])

    canvas.getBoundingClientRect = () => ({ ...RECT, width: 1000 }) as DOMRect
    const outside = pointer('pointerdown', 10, 10)
    canvas.dispatchEvent(outside)
    expect(outside.defaultPrevented).toBe(false)
    expect(socket().sent).toHaveLength(7)
  })

  it('takes hold of the pointer where the browser can', async () => {
    const { canvas } = await live()
    const capture = vi.fn()
    canvas.setPointerCapture = capture
    canvas.dispatchEvent(pointer('pointerdown', 201, 437, { id: 7 }))
    expect(capture).toHaveBeenCalledWith(7)
  })

  it('scrolls by what the wheel moved, at most every MOVE_MS, from over the screen only', async () => {
    vi.useFakeTimers(INPUT_CLOCK)
    const { canvas } = await live()
    const wheel = (deltaY: number) => new WheelEvent('wheel', { clientX: 201, clientY: 437, deltaY, cancelable: true })
    const first = wheel(30)
    canvas.dispatchEvent(first)
    canvas.dispatchEvent(wheel(40))
    canvas.dispatchEvent(wheel(5))
    vi.advanceTimersByTime(MOVE_MS)
    canvas.getBoundingClientRect = () => ({ ...RECT, width: 1000 }) as DOMRect
    const outside = new WheelEvent('wheel', { clientX: 5, clientY: 5, deltaY: 10, cancelable: true })
    canvas.dispatchEvent(outside)
    vi.advanceTimersByTime(MOVE_MS)
    expect(first.defaultPrevented).toBe(true)
    expect(outside.defaultPrevented).toBe(true)
    expect(socket().sent).toEqual([
      { type: 'scroll', nx: 0.5, ny: 0.5, dy: 30 },
      { type: 'scroll', nx: 0.5, ny: 0.5, dy: 45 },
    ])
    canvas.dispatchEvent(new MouseEvent('contextmenu', { cancelable: true }))
  })

  it('types in bursts, names the keys the device knows, and leaves shortcuts and paste to the browser', async () => {
    vi.useFakeTimers(CLOCK)
    const { canvas, host } = await live()
    const outer = vi.fn()
    host.addEventListener('keydown', outer)
    const key = (init: KeyboardEventInit) => {
      const event = new KeyboardEvent('keydown', { cancelable: true, bubbles: true, ...init })
      canvas.dispatchEvent(event)
      return event
    }
    key({ key: 'T' })
    key({ key: 'r' })
    vi.advanceTimersByTime(TYPE_SETTLE_MS)
    key({ key: 'i' })
    key({ key: 'Enter' })
    key({ key: 'ArrowUp' })
    const shortcut = key({ key: 'v', metaKey: true })
    key({ key: 'a', ctrlKey: true })
    const shift = key({ key: 'Shift' })
    key({ key: 'p' })
    const paste = new Event('paste', { cancelable: true }) as ClipboardEvent
    Object.defineProperty(paste, 'clipboardData', { value: { getData: () => 'x'.repeat(TEXT_MAX_CHARS + 5) } })
    canvas.dispatchEvent(paste)
    const empty = new Event('paste') as ClipboardEvent
    Object.defineProperty(empty, 'clipboardData', { value: null })
    canvas.dispatchEvent(empty)
    for (let i = 0; i < TEXT_MAX_CHARS; i++) key({ key: 'z' })
    expect(socket().sent).toEqual([
      { type: 'text', text: 'Tr' },
      { type: 'text', text: 'i' },
      { type: 'key', name: 'return' },
      { type: 'key', name: 'up' },
      { type: 'text', text: 'p' },
      { type: 'text', text: 'x'.repeat(TEXT_MAX_CHARS) },
      { type: 'text', text: 'z'.repeat(TEXT_MAX_CHARS) },
    ])
    expect(shortcut.defaultPrevented).toBe(false)
    expect(shift.defaultPrevented).toBe(false)
    expect(outer).not.toHaveBeenCalled()
  })

  it('presses Home and Lock, and switches the appearance only when the device can hear it', async () => {
    const { view, $ } = await live()
    $<HTMLButtonElement>('[data-smv="home"]').click()
    $<HTMLButtonElement>('[data-smv="lock"]').click()
    const appearance = $<HTMLButtonElement>('[data-smv="appearance"]')
    appearance.click()
    expect(appearance.getAttribute('aria-pressed')).toBe('true')
    expect(appearance.title).toBe('Light appearance')
    expect(appearance.querySelector('svg[data-icon="sun"]')).not.toBeNull()
    appearance.click()
    expect(appearance.getAttribute('aria-pressed')).toBe('false')
    expect(appearance.querySelector('svg[data-icon="moon"]')).not.toBeNull()
    expect(socket().sent).toEqual([
      { type: 'button', name: 'home' }, { type: 'button', name: 'lock' },
      { type: 'appearance', mode: 'dark' }, { type: 'appearance', mode: 'light' },
    ])
    view.setActive(false)
    appearance.click()
    expect(appearance.getAttribute('aria-pressed')).toBe('false')
    view.el.querySelector<HTMLElement>('.smv-bar')!.click()
  })

  it('mirrors a device it cannot touch: says so, offers nothing that would not reach it, and sends no input', async () => {
    const hello: ServerHello = {
      ...HELLO, encodings: ['jpeg'], connector: 'simctl', capabilities: VIEW_ONLY,
      fallback_reason: 'idb_companion is not installed',
    }
    const { canvas, $, onState } = await live({}, hello)
    const mode = $<HTMLElement>('[data-smv-mode]')
    expect([mode.hidden, mode.title]).toEqual([false, 'idb_companion is not installed'])
    expect($<HTMLButtonElement>('[data-smv="home"]').hidden).toBe(true)
    expect($<HTMLButtonElement>('[data-smv="lock"]').hidden).toBe(true)
    expect($<HTMLButtonElement>('[data-smv="appearance"]').hidden).toBe(false)
    expect($<HTMLButtonElement>('[data-smv="devices"]').hidden).toBe(false)
    canvas.dispatchEvent(pointer('pointerdown', 201, 437))
    canvas.dispatchEvent(new WheelEvent('wheel', { clientX: 201, clientY: 437, deltaY: 30, cancelable: true }))
    canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', cancelable: true }))
    canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', cancelable: true }))
    const paste = new Event('paste', { cancelable: true }) as ClipboardEvent
    Object.defineProperty(paste, 'clipboardData', { value: { getData: () => 'hello' } })
    canvas.dispatchEvent(paste)
    $<HTMLButtonElement>('[data-smv="appearance"]').click()
    expect(socket().sent).toEqual([{ type: 'appearance', mode: 'dark' }])
    expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ connector: 'simctl', viewOnly: true }))
    socket().message({ ...hello, fallback_reason: null })
    expect(mode.title).toBe('The simctl connector shows the screen but cannot touch it.')
  })

  it('lists this Mac’s simulators, marks the one in use, and switches to another', async () => {
    const { view, transport, $ } = await live()
    const first = socket()
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    expect($('[data-smv-picker]').textContent).toBe('Looking for simulators…')
    await flush()
    const rows = view.el.querySelectorAll<HTMLButtonElement>('[data-smv-udid]')
    expect([...rows].map((row) => row.getAttribute('aria-checked'))).toEqual(['true', 'false'])
    expect(rows[1].innerHTML).toContain('iPhone &lt;Air&gt;')
    rows[0].click()
    expect($<HTMLElement>('[data-smv-picker]').hidden).toBe(true)
    expect(transport.choose).not.toHaveBeenCalled()

    $<HTMLButtonElement>('[data-smv="devices"]').click()
    await flush()
    view.el.querySelectorAll<HTMLButtonElement>('[data-smv-udid]')[1].click()
    expect(transport.choose).toHaveBeenCalledWith('U2')
    await flush()
    expect(first.closed).toBe(true)
    expect(transport.start).toHaveBeenCalledTimes(2)
    expect(FakeSocket.instances).toHaveLength(2)
    expect(said(view)).toBe('Starting iPhone 17 Pro… 2s')
  })

  it('says when the simulators cannot be listed or used, and stays closed when closed while listing', async () => {
    const devices = vi.fn()
      .mockRejectedValueOnce(new Error('simctl failed'))
      .mockRejectedValueOnce(new Error(''))
      .mockResolvedValueOnce([])
      .mockResolvedValue([{ udid: 'U2', name: 'iPhone Air', runtime: 'iOS 26.5', state: 'Shutdown', created: false }])
    const choose = vi.fn().mockRejectedValueOnce(new Error('No such simulator')).mockRejectedValueOnce(new Error(''))
    const { view, $ } = await live({ transport: { devices, choose } })
    const open = async () => {
      $<HTMLButtonElement>('[data-smv="devices"]').click()
      await flush()
      return $('[data-smv-picker]').textContent
    }
    expect(await open()).toBe('simctl failed')
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    expect(await open()).toBe('The simulators could not be listed.')
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    expect(await open()).toBe('No iOS simulators on this Mac.Shut down this device')
    $<HTMLButtonElement>('[data-smv="devices"]').click()

    $<HTMLButtonElement>('[data-smv="devices"]').click()
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    await flush()
    expect($<HTMLElement>('[data-smv-picker]').hidden).toBe(true)

    let fail: (error: Error) => void = () => {}
    devices.mockImplementationOnce(() => new Promise((_, reject) => { fail = reject }))
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    fail(new Error('late'))
    await flush()
    expect($('[data-smv-picker]').textContent).toBe('')

    await open()
    view.el.querySelector<HTMLButtonElement>('[data-smv-udid]')!.click()
    await flush()
    expect(said(view)).toContain('No such simulator')
    await open()
    view.el.querySelector<HTMLButtonElement>('[data-smv-udid]')!.click()
    await flush()
    expect(said(view)).toContain('That simulator could not be used.')
    expect($('[data-smv="start"]').textContent).toBe('Try again')
  })

  it('lets the device go, or shuts it down, and starts again on request', async () => {
    const stop = vi.fn().mockRejectedValueOnce(new Error('gone')).mockResolvedValue(true)
    const { view, transport, $ } = await live({ transport: { stop } })
    $<HTMLButtonElement>('[data-smv="stop"]').click()
    await flush()
    expect(stop).toHaveBeenLastCalledWith(false)
    expect(socket().closed).toBe(true)
    expect(said(view)).toContain('its device keeps running for next time')
    $<HTMLButtonElement>('[data-smv="start"]').click()
    await flush()
    expect(transport.start).toHaveBeenCalledTimes(2)
    socket().open()
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    await flush()
    $<HTMLButtonElement>('[data-smv="shutdown"]').click()
    expect(stop).toHaveBeenLastCalledWith(true)
    expect(said(view)).toContain('The device is shut down.')
  })

  it('says why the screen went away, whether trying again can help, and puts the pill in step', async () => {
    const { view, $ } = await live()
    const pill = () => $<HTMLElement>('[data-smv-state]').textContent
    socket().end(CLOSE_STOPPED, 'the simulator stopped')
    expect(said(view)).toContain('The simulator stopped.')
    expect($('[data-smv="start"]').textContent).toBe('Start the simulator')
    expect(pill()).toBe('Stopped')
    const ends: Array<[number, string, string, boolean, string]> = [
      [CLOSE_FORBIDDEN, 'Turned off in its settings', 'Turned off in its settings', true, 'Stopped'],
      [CLOSE_FORBIDDEN, '', 'The simulator is off here.', true, 'Stopped'],
      [CLOSE_UNSUPPORTED, 'protocol version 2 is not supported', 'protocol version 2 is not supported', true, 'Stopped'],
      [CLOSE_BAD_MESSAGE, '', 'This viewer and the server do not speak the same protocol.', true, 'Stopped'],
      [1014, 'companion gone', "The simulator's screen closed: companion gone Reconnecting…", false, 'Reconnecting'],
      [1006, '', 'The connection to the simulator ended. Reconnecting…', false, 'Reconnecting'],
    ]
    for (const [code, reason, message, retry, state] of ends) {
      view.el.querySelector<HTMLButtonElement>('[data-smv="start"]')?.click()
      view.setActive(false)
      view.setActive(true)
      await flush()
      socket().message({ type: 'status', ...DEVICE })
      socket().end(code, reason)
      expect(said(view)).toContain(message)
      expect(view.el.querySelector('[data-smv="start"]') !== null).toBe(retry)
      expect(pill()).toBe(state)
    }
    view.destroy()
  })

  it('reconnects by itself when a live device’s connection drops, a few times, then offers to try again', async () => {
    vi.useFakeTimers(CLOCK)
    const { view, transport, $ } = await live()
    const pill = () => $<HTMLElement>('[data-smv-state]').textContent
    socket().end(1006, '')
    expect(said(view)).toBe('The connection to the simulator ended. Reconnecting…')
    expect(pill()).toBe('Reconnecting')
    vi.advanceTimersByTime(RECONNECT_MS[0] - 1)
    await flush()
    expect(transport.start).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(1)
    await flush()
    expect(transport.start).toHaveBeenCalledTimes(2)
    socket().open()
    // Ready again: the next drop starts counting afresh.
    socket().message({ type: 'status', ...DEVICE })
    for (const wait of RECONNECT_MS) {
      socket().end(1006, '')
      expect(view.el.querySelector('[data-smv="start"]')).toBeNull()
      vi.advanceTimersByTime(wait)
      await flush()
    }
    expect(transport.start).toHaveBeenCalledTimes(2 + RECONNECT_MS.length)
    socket().end(1006, '')
    expect(said(view)).toContain('The connection to the simulator ended.')
    expect($('[data-smv="start"]').textContent).toBe('Try again')
    expect(pill()).toBe('Stopped')
    // A device that had failed is not reconnected to by itself.
    $<HTMLButtonElement>('[data-smv="start"]').click()
    await flush()
    socket().message({ type: 'status', ...DEVICE, state: 'failed', reason: 'Boot timed out' })
    socket().end(1006, '')
    expect($('[data-smv="start"]').textContent).toBe('Try again')
    view.destroy()
  })

  it('reconnects by itself when the device is restarted, even after dropped connections used up the attempts', async () => {
    vi.useFakeTimers(CLOCK)
    const { view, transport, $ } = await live()
    for (const wait of RECONNECT_MS) {
      socket().end(1006, '')
      vi.advanceTimersByTime(wait)
      await flush()
    }
    const starts = transport.start.mock.calls.length
    socket().end(CLOSE_RESTARTING, 'the simulator is restarting')
    expect(said(view)).toBe('The simulator is restarting. Reconnecting…')
    expect(view.el.querySelector('[data-smv="start"]')).toBeNull()
    expect($<HTMLElement>('[data-smv-state]').textContent).toBe('Reconnecting')
    vi.advanceTimersByTime(RECONNECT_MS[0])
    await flush()
    expect(transport.start).toHaveBeenCalledTimes(starts + 1)
    view.destroy()
  })

  it('asks for a fresh start when trying again after a failed start, though its screen is still open', async () => {
    const { view, transport, $ } = await live()
    socket().message({ type: 'status', ...DEVICE, state: 'failed', reason: 'Boot timed out' })
    const waiting = socket()
    expect($('[data-smv="start"]').textContent).toBe('Try again')
    $<HTMLButtonElement>('[data-smv="start"]').click()
    await flush()
    expect(waiting.closed).toBe(true)
    expect(transport.start).toHaveBeenCalledTimes(2)
    expect(socket()).not.toBe(waiting)
    view.destroy()
  })

  it('reconnects after a restart even when the stopped status reached it before the close', async () => {
    vi.useFakeTimers(CLOCK)
    const { view, transport } = await live()
    socket().message({ type: 'status', ...DEVICE, state: 'stopped' })
    socket().end(CLOSE_RESTARTING, 'the simulator is restarting')
    expect(said(view)).toBe('The simulator is restarting. Reconnecting…')
    vi.advanceTimersByTime(RECONNECT_MS[0])
    await flush()
    expect(transport.start).toHaveBeenCalledTimes(2)
    view.destroy()
  })

  it('paints nothing and starts no timer when it is closed or put away while its start is on the way', async () => {
    vi.useFakeTimers(CLOCK)
    const pending: Array<(value: Started) => void> = []
    const start = vi.fn(() => new Promise<Started>((resolve) => pending.push(resolve)))
    const closed = setup({ transport: { start } })
    closed.view.setActive(true)
    await flush()
    closed.view.destroy()
    pending[0](started())
    await flush()
    expect(vi.getTimerCount()).toBe(0)
    const away = setup({ transport: { start } })
    away.view.setActive(true)
    await flush()
    away.view.setActive(false)
    pending[1](started())
    await flush()
    expect(vi.getTimerCount()).toBe(0)
    expect(said(away.view)).not.toContain('Starting')
    away.view.destroy()
  })

  it('keeps trying while the server is away, then says why it could not', async () => {
    vi.useFakeTimers(CLOCK)
    const start = vi.fn(async () => started({ ...DEVICE }))
    const { view, $ } = await live({ transport: { start } })
    start.mockRejectedValue(new Error('Failed to fetch'))
    socket().end(1006, '')
    for (const wait of RECONNECT_MS) {
      expect(said(view)).toBe('The connection to the simulator ended. Reconnecting…')
      expect(view.el.querySelector('[data-smv="start"]')).toBeNull()
      vi.advanceTimersByTime(wait)
      await flush()
    }
    expect(start).toHaveBeenCalledTimes(1 + RECONNECT_MS.length)
    expect(said(view)).toContain('Failed to fetch')
    expect($('[data-smv="start"]').textContent).toBe('Try again')
    expect($<HTMLElement>('[data-smv-state]').textContent).toBe('Failed')
    socket().end(1006, '')
    view.setActive(false)
    view.destroy()
  })

  it('says why the simulator would not start', async () => {
    const start = vi.fn().mockRejectedValueOnce(new Error('The iOS Simulator is off for this project.'))
      .mockRejectedValueOnce(new Error(''))
    const { view, $ } = setup({ transport: { start } })
    view.setActive(true)
    await flush()
    expect(said(view)).toContain('The iOS Simulator is off for this project.')
    expect($<HTMLElement>('[data-smv-state]').textContent).toBe('Failed')
    $<HTMLButtonElement>('[data-smv="start"]').click()
    await flush()
    expect(said(view)).toContain('The simulator could not start.')
  })

  it('draws what an agent does over the screen and says who is using the device, or what it is busy with', async () => {
    vi.useFakeTimers(CLOCK)
    const { view, $ } = await live()
    const badge = $<HTMLElement>('[data-smv-badge]')
    expect(badge.hidden).toBe(true)
    socket().message({ type: 'status', ...DEVICE, busy: 'running tests (b1)' })
    expect(badge.textContent).toBe('Busy: running tests (b1)')
    socket().message({
      type: 'agent', id: 'a1', phase: 'intent', agent: { key: 'k', title: 'Claude Code · notes' },
      gesture: { kind: 'tap', duration_ms: 80, points: [[0.5, 0.5]] }, label: '', caption: '', lead_ms: 250,
      pointer: true,
    })
    expect(badge.textContent).toBe('Claude Code · notes is using this device')
    expect($<HTMLElement>('[data-ac-pointer]').style.transform).toBe('translate(201px, 437px)')
    vi.advanceTimersByTime(ACTIVE_MS)
    expect(badge.textContent).toBe('Busy: running tests (b1)')
    socket().message({ type: 'status', ...DEVICE })
    expect(badge.hidden).toBe(true)
    expect(view.device?.busy).toBeNull()
  })

  it('measures an agent’s gesture against the canvas before the device says its size', async () => {
    const { view, $ } = setup({ transport: { start: vi.fn(async () => started({ ...DEVICE, state: 'booting', screen: null })) } })
    view.setActive(true)
    await flush()
    socket().open()
    socket().message({
      type: 'agent', id: 'a1', phase: 'intent', agent: { key: 'k', title: 'Claude' },
      gesture: { kind: 'tap', duration_ms: 80, points: [[1, 1]] }, label: '', caption: '', lead_ms: 0, pointer: true,
    })
    expect($<HTMLElement>('[data-ac-pointer]').style.transform).toBe('translate(402px, 874px)')
  })

  it('offers to undock, dock and open a page as its placement allows, and closes on request', () => {
    const { view, options, $ } = setup()
    const place = $<HTMLButtonElement>('[data-smv="place"]')
    const page = $<HTMLAnchorElement>('[data-smv-page]')
    expect(view.el.dataset.placement).toBe('dock')
    expect(place.title).toBe('Undock into a window')
    expect(place.querySelector('svg[data-icon="undock"]')).not.toBeNull()
    expect(page.hidden).toBe(false)
    expect(page.getAttribute('href')).toBe('/viewer/tp-1')
    place.click()
    expect(options.onPlace).toHaveBeenLastCalledWith('window')
    view.setPlacement('window')
    expect(place.title).toBe('Dock beside the page')
    expect(place.querySelector('svg[data-icon="dock"]')).not.toBeNull()
    place.click()
    expect(options.onPlace).toHaveBeenLastCalledWith('dock')
    view.setPlacement('page')
    expect(place.hidden).toBe(true)
    expect(page.hidden).toBe(true)
    $<HTMLButtonElement>('[data-smv="close"]').click()
    expect(options.onClose).toHaveBeenCalled()

    const bare = setup({ onPlace: undefined, pageHref: undefined, onClose: undefined, onState: undefined })
    expect(bare.$<HTMLButtonElement>('[data-smv="place"]').hidden).toBe(true)
    expect(bare.$<HTMLAnchorElement>('[data-smv-page]').hidden).toBe(true)
    expect(bare.$<HTMLButtonElement>('[data-smv="close"]').hidden).toBe(true)
    bare.$<HTMLButtonElement>('[data-smv="place"]').click()
    bare.$<HTMLButtonElement>('[data-smv="close"]').click()
    bare.view.setActive(true)
  })

  it('draws with a host’s own icons, and puts its styles in the document once however many viewers there are', () => {
    const drawn = setup({ icon: (name) => `<i data-host-icon="${name}"></i>` })
    setup()
    expect(drawn.$('[data-smv="home"] [data-host-icon="home"]')).not.toBeNull()
    expect(drawn.$('[data-ac-pointer] [data-host-icon="pointer"]')).not.toBeNull()
    expect(document.head.querySelectorAll(`style#${STYLE_ID}`)).toHaveLength(1)
  })

  it('lets go of everything when destroyed', async () => {
    vi.useFakeTimers(INPUT_CLOCK)
    const { view, host, canvas, $ } = setup()
    view.focus()
    expect(document.activeElement).toBe(canvas)
    view.setActive(true)
    await flush()
    hear()
    canvas.dispatchEvent(pointer('pointerdown', 201, 437))
    canvas.dispatchEvent(pointer('pointermove', 100, 100))
    canvas.dispatchEvent(pointer('pointermove', 150, 150))
    canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'q', cancelable: true }))
    socket().message({ type: 'status', ...DEVICE, state: 'booting' })
    const ws = socket()
    view.destroy()
    expect(ws.closed).toBe(true)
    expect(host.children).toHaveLength(0)
    vi.advanceTimersByTime(10_000)
    expect(ws.sent.filter((m) => (m as { type: string }).type === 'text')).toEqual([])
    // The second move was still waiting: destroyed, it never goes.
    expect(ws.sent.filter((m) => (m as { phase?: string }).phase === 'move')).toHaveLength(1)
    view.setActive(false)
    view.setActive(true)
    await flush()
    expect(FakeSocket.instances).toHaveLength(1)
    expect($('[data-smv-empty]').textContent).toContain('Starting')
  })

  it('sends what was typed before going inactive', async () => {
    const { view, canvas } = await live()
    canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'q', cancelable: true }))
    const ws = socket()
    view.setActive(false)
    expect(ws.sent).toEqual([{ type: 'text', text: 'q' }])
  })
})

describe('H.264 frames', () => {
  const h264 = () => socket().message(new Uint8Array([0x02, 0, 0, 1, 0x65]).buffer)

  it('offers H.264 first where this page decodes it, and only JPEG where it cannot', async () => {
    const decodes = setup({ canDecodeH264: () => true })
    decodes.view.setActive(true)
    await flush()
    socket().open()
    expect(socket().sent[0]).toEqual({ type: 'hello', v: 1, encodings: ['h264', 'jpeg'] })
    for (const canDecodeH264 of [() => false, undefined]) {
      const cannot = setup({ canDecodeH264 })
      cannot.view.setActive(true)
      await flush()
      socket().open()
      expect(socket().sent[0]).toEqual({ type: 'hello', v: 1, encodings: ['jpeg'] })
    }
  })

  it('decodes H.264 frames into the canvas through one decoder per connection', async () => {
    const { canvas } = await live({ canDecodeH264: () => true })
    h264()
    socket().message(new Uint8Array([0x02, 7]).buffer)
    expect(decoding.sinks).toHaveLength(1)
    const [sink] = decoding.sinks
    expect(sink.push).toHaveBeenCalledTimes(2)
    expect(Array.from(sink.push.mock.calls[0][0] as Uint8Array)).toEqual([0, 0, 1, 0x65])
    const picture = { displayWidth: 1206, displayHeight: 2622 }
    sink.options.draw(picture)
    expect(drawImage).toHaveBeenCalledWith(picture, 0, 0)
    expect([canvas.width, canvas.height]).toEqual([1206, 2622])
    socket().message(new Uint8Array([0x02]).buffer)
    expect(sink.push).toHaveBeenCalledTimes(2)
  })

  it('falls back to JPEG for as long as the page is open when this page cannot decode the stream', async () => {
    await live({ canDecodeH264: () => true })
    h264()
    const first = socket()
    decoding.sinks[0].options.onError(new Error('unsupported'))
    await flush()
    expect(first.closed).toBe(true)
    expect(decoding.sinks[0].close).toHaveBeenCalledOnce()
    expect(FakeSocket.instances).toHaveLength(2)
    socket().open()
    expect(socket().sent[0]).toEqual({ type: 'hello', v: 1, encodings: ['jpeg'] })
  })

  it('lets the decoder go with its connection: ended, let go, switched, inactive or destroyed', async () => {
    const { view, $ } = await live({ canDecodeH264: () => true })
    h264()
    socket().end(CLOSE_STOPPED, 'the simulator stopped')
    expect(decoding.sinks[0].close).toHaveBeenCalledOnce()

    $<HTMLButtonElement>('[data-smv="start"]').click()
    await flush()
    socket().open()
    h264()
    $<HTMLButtonElement>('[data-smv="stop"]').click()
    expect(decoding.sinks[1].close).toHaveBeenCalledOnce()

    $<HTMLButtonElement>('[data-smv="start"]').click()
    await flush()
    socket().open()
    h264()
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    await flush()
    view.el.querySelectorAll<HTMLButtonElement>('[data-smv-udid]')[1].click()
    await flush()
    expect(decoding.sinks[2].close).toHaveBeenCalledOnce()

    socket().open()
    h264()
    view.setActive(false)
    expect(decoding.sinks[3].close).toHaveBeenCalledOnce()
    view.setActive(true)
    await flush()
    socket().open()
    h264()
    view.destroy()
    expect(decoding.sinks[4].close).toHaveBeenCalledOnce()
  })
})
