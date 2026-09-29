// SPDX-License-Identifier: Apache-2.0
/**
 * A viewer on a fake transport and a fake screen socket, for the viewer's tests: `setup` makes one, `live` brings it
 * up and says hello as a server would, and `hear` says what a server says as a stream starts.
 */
import { vi } from 'vitest'

import { FakeSocket } from './fake-socket'
import {
  CAPABILITIES, type Capability, type Device, type ServerHello, type Started,
} from '../src/protocol.generated'
import type { SimMirrorTransport } from '../src/transport'
import { createViewer, type ViewerOptions } from '../src/viewer'

/** Timers and the date are faked. */
export const CLOCK = { toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] } as Parameters<
  typeof vi.useFakeTimers>[0]
/** The same with `performance.now`, which times moves and scrolls. */
export const INPUT_CLOCK = {
  toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date', 'performance'],
} as Parameters<typeof vi.useFakeTimers>[0]

export const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

export const FULL: Capability[] = CAPABILITIES.filter((capability) => capability !== 'build_preview')
export const VIEW_ONLY: Capability[] = ['lifecycle', 'device_list', 'appearance', 'screenshot', 'stream_jpeg']
export const HELLO: ServerHello = {
  type: 'hello', v: 1, server: 'sim-mirror/0.1.0', encodings: ['h264', 'jpeg'], connector: 'idb', capabilities: FULL,
  fallback_reason: null,
}

export const DEVICE: Device = {
  udid: 'U1', name: 'iPhone 17 Pro', runtime: 'iOS 26.5', state: 'ready', reason: null, since_ms: 0, viewers: 1,
  busy: null, created: true, booted_by_us: true,
  screen: { points: { w: 402, h: 874 }, pixels: { w: 1206, h: 2622 }, scale: 3 }, app_hierarchy: null,
  kind: 'simulator', connection: null, recording: null,
}

export function started(device: Device | null = { ...DEVICE, state: 'booting', since_ms: 2000 }): Started {
  return {
    enabled: true, reason: null, device, stream: { encoding: 'auto', fps: 30 }, cursor: { enabled: true, lead_ms: 250 },
    connector: 'idb', capabilities: FULL, fallback_reason: null, ticket: 'tk',
  }
}

export type Calls = Partial<Record<keyof SimMirrorTransport, unknown>>

export function transportWith(overrides: Calls = {}) {
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

export const RECT = { left: 0, top: 0, width: 402, height: 874, right: 402, bottom: 874, x: 0, y: 0, toJSON: () => ({}) }

export type SetupOptions = Partial<Omit<ViewerOptions, 'transport'>> & { transport?: Calls }

export function setup(overrides: SetupOptions = {}) {
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

export const socket = () => FakeSocket.last()
export const said = (view: { el: HTMLElement }) => view.el.querySelector<HTMLElement>('[data-smv-empty]')!.textContent

export function pointer(type: string, x: number, y: number, init: { id?: number; button?: number } = {}) {
  const event = new MouseEvent(type, { clientX: x, clientY: y, button: init.button ?? 0, bubbles: true, cancelable: true })
  Object.defineProperty(event, 'pointerId', { value: init.id ?? 1 })
  return event
}

export function frame(tag: number, size = 4): ArrayBuffer {
  const bytes = new Uint8Array(size)
  bytes[0] = tag
  return bytes.buffer
}

/** Open the socket, say hello as a server, start the stream, and say the device is ready. */
export function hear(hello: ServerHello = HELLO, device: Device = DEVICE) {
  socket().open()
  socket().message(hello)
  socket().message({ type: 'stream', encoding: 'jpeg' })
  socket().message({ type: 'status', ...device })
}

export async function live(overrides: SetupOptions = {}, hello: ServerHello = HELLO) {
  const rig = setup(overrides)
  rig.view.setActive(true)
  await flush()
  hear(hello)
  socket().sent = []
  return rig
}

