// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { FakeSocket } from '../test-support/fake-socket'
import { DEVICE, HELLO, INPUT_CLOCK, live, pointer, socket } from '../test-support/viewer-harness'
import { buttonsOf, deviceNoun, isReal, kindChip } from './device-kind'
import type { Device, ServerHello } from './protocol.generated'
import { MOVE_MS } from './viewer-input'

const MAC: Device = { ...DEVICE, kind: 'mac', udid: 'macapp-dev.example.Sample-460bfce7', name: 'Sample', runtime: 'macOS', buttons: [] }
const HELLO_MAC: ServerHello = { ...HELLO, connector: 'mac-app', capabilities: ['input_touch', 'input_pointer', 'input_text', 'input_key', 'device_list'] }
beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 1206, height: 2622, close: vi.fn() })))
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as never
})
afterEach(() => { document.body.innerHTML = ''; vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('names a macOS app and offers no device hardware controls', async () => {
  const { $, view } = await live({}, HELLO_MAC)
  socket().message({ type: 'status', ...MAC })
  expect(deviceNoun(MAC)).toBe('app')
  expect(kindChip(MAC)).toBe('macOS app')
  expect(isReal(MAC)).toBe(false)
  expect(buttonsOf(MAC)).toEqual([])
  expect($<HTMLButtonElement>('[data-smv="home"]').hidden).toBe(true)
  expect($<HTMLButtonElement>('[data-smv="appearance"]').hidden).toBe(true)
  view.destroy()
})

it('sends secondary clicks, bounded hover, horizontal wheel and app chords only when advertised', async () => {
  vi.useFakeTimers(INPUT_CLOCK)
  const { canvas, view } = await live({}, HELLO_MAC)
  socket().message({ type: 'status', ...MAC })
  canvas.dispatchEvent(pointer('pointerdown', 201, 437, { button: 2 }))
  canvas.dispatchEvent(pointer('pointerdown', -10, -10, { button: 2 }))
  canvas.dispatchEvent(pointer('pointermove', 201, 437))
  canvas.dispatchEvent(pointer('pointermove', -10, -10))
  vi.advanceTimersByTime(MOVE_MS)
  canvas.dispatchEvent(new WheelEvent('wheel', { clientX: 201, clientY: 437, deltaX: 10, deltaY: 20 }))
  canvas.dispatchEvent(new WheelEvent('wheel', { clientX: 201, clientY: 437, deltaX: 5, deltaY: 7 }))
  vi.advanceTimersByTime(MOVE_MS)
  canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 's', metaKey: true, shiftKey: true }))
  canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', ctrlKey: true, altKey: true }))
  canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'F2' }))
  const paste = new KeyboardEvent('keydown', { key: 'v', metaKey: true, cancelable: true })
  canvas.dispatchEvent(paste)
  expect(paste.defaultPrevented).toBe(false)
  expect(socket().sent).toEqual([
    { type: 'pointer', action: 'secondary', nx: 0.5, ny: 0.5 },
    { type: 'pointer', action: 'hover', nx: 0.5, ny: 0.5 },
    { type: 'wheel', nx: 0.5, ny: 0.5, dx: 10, dy: 20 },
    { type: 'wheel', nx: 0.5, ny: 0.5, dx: 5, dy: 7 },
    { type: 'chord', chord: 'cmd+shift+s' },
    { type: 'chord', chord: 'ctrl+alt+left' },
    { type: 'chord', chord: 'f2' },
  ])
  view.destroy()
})
