// SPDX-License-Identifier: Apache-2.0
/**
 * What a person does to the screen, sent as the protocol's input messages -- and nothing else.
 *
 * A finger's touches as shares of the screen, a scroll, a named key, text: never an install, a launch or an address.
 * Each is sent only when the device's connector takes it, so a view-only mirror sends nothing. Moves and scrolls go at
 * most one every `MOVE_MS` -- timed, not painted, so a page whose window is hidden still sends them -- and a move still
 * waiting goes before the finger lifts. Typing goes in bursts, each one becoming a paste on the device, so a word is
 * one message, not five. Cmd/Ctrl+V is left to the browser, whose `paste` event is the only way to read the clipboard.
 */
import { TEXT_MAX_CHARS, type Capability, type ClientInput, type KeyName } from './protocol.generated'
import type { ScreenCanvas } from './screen-canvas'

/** The keys that reach the device by name, from `KeyboardEvent.key` to what the protocol calls them. */
export const KEY_MAP: Readonly<Record<string, KeyName>> = {
  Enter: 'return', Escape: 'escape', Backspace: 'delete', Tab: 'tab',
  ArrowRight: 'right', ArrowLeft: 'left', ArrowDown: 'down', ArrowUp: 'up',
}
export const TYPE_SETTLE_MS = 120
/** The least time between two moves, or two scrolls, sent: about a frame at 60 Hz. */
export const MOVE_MS = 16

/** A share of the screen, to four places: enough for a point, and a short message. */
const share = (value: number): number => Math.round(value * 10000) / 10000

export interface InputOptions {
  canvas: HTMLCanvasElement
  screen: ScreenCanvas
  send(message: ClientInput): boolean
  allows(capability: Capability): boolean
}

export interface ViewerInput {
  /** Send what was typed but not sent yet. */
  flushTyped(): void
  /** The connection ended: no finger is down any more. */
  releaseFinger(): void
  destroy(): void
}

export function attachInput({ canvas, screen, send, allows }: InputOptions): ViewerInput {
  /** The pointer a touch is following; one finger at a time. */
  let finger: number | null = null
  let move: { nx: number; ny: number } | null = null
  let wheel: { nx: number; ny: number; dy: number } | null = null
  let pending: number | null = null
  /** When the last move or scroll went. */
  let sentAt = Number.NEGATIVE_INFINITY
  let typed = ''
  let typeTimer: number | null = null

  function flushPointer(): void {
    if (pending !== null) window.clearTimeout(pending)
    pending = null
    sentAt = performance.now()
    if (move && finger !== null) send({ type: 'touch', phase: 'move', ...move })
    move = null
    if (wheel) send({ type: 'scroll', ...wheel })
    wheel = null
  }

  /** Send what waits at once when `MOVE_MS` has passed since the last send, else once it has -- on a timer, not a
   * paint: a page whose window is hidden does not paint, and its moves must still go. */
  function schedule(): void {
    const wait = sentAt + MOVE_MS - performance.now()
    if (wait <= 0) flushPointer()
    else pending ??= window.setTimeout(flushPointer, wait)
  }

  function flushTyped(): void {
    if (typeTimer !== null) window.clearTimeout(typeTimer)
    typeTimer = null
    if (typed) send({ type: 'text', text: typed })
    typed = ''
  }

  canvas.addEventListener('pointerdown', (event) => {
    if (finger !== null || event.button !== 0 || !allows('input_touch')) return
    const at = screen.point(event)
    if (!at.inside) return
    event.preventDefault()
    finger = event.pointerId
    if (typeof canvas.setPointerCapture === 'function') canvas.setPointerCapture(event.pointerId)
    canvas.focus()
    send({ type: 'touch', phase: 'down', nx: share(at.nx), ny: share(at.ny) })
  })
  canvas.addEventListener('pointermove', (event) => {
    if (event.pointerId !== finger) return
    const at = screen.point(event)
    move = { nx: share(at.nx), ny: share(at.ny) }
    schedule()
  })
  const lift = (phase: 'up' | 'cancel') => (event: PointerEvent) => {
    if (event.pointerId !== finger) return
    // A move still waiting is where the finger went before it lifted: it goes first.
    if (move) flushPointer()
    const at = screen.point(event)
    finger = null
    send({ type: 'touch', phase, nx: share(at.nx), ny: share(at.ny) })
  }
  canvas.addEventListener('pointerup', lift('up'))
  canvas.addEventListener('pointercancel', lift('cancel'))
  canvas.addEventListener('wheel', (event) => {
    event.preventDefault()
    const at = screen.point(event)
    if (!at.inside || !allows('input_touch')) return
    wheel = { nx: share(at.nx), ny: share(at.ny), dy: (wheel?.dy ?? 0) + event.deltaY }
    schedule()
  }, { passive: false })
  canvas.addEventListener('contextmenu', (event) => event.preventDefault())
  canvas.addEventListener('keydown', (event) => {
    // The device gets the keys; the page's own shortcuts do not see them.
    event.stopPropagation()
    if (event.metaKey || event.ctrlKey) return
    const name = KEY_MAP[event.key]
    if (name) {
      if (!allows('input_key')) return
      event.preventDefault()
      flushTyped()
      send({ type: 'key', name })
    } else if (event.key.length === 1 && allows('input_text')) {
      event.preventDefault()
      typed += event.key
      if (typed.length >= TEXT_MAX_CHARS) flushTyped()
      else if (typeTimer === null) typeTimer = window.setTimeout(flushTyped, TYPE_SETTLE_MS)
    }
  })
  canvas.addEventListener('paste', (event) => {
    const text = event.clipboardData?.getData('text/plain')
    if (!text || !allows('input_text')) return
    event.preventDefault()
    flushTyped()
    send({ type: 'text', text: text.slice(0, TEXT_MAX_CHARS) })
  })

  return {
    flushTyped,
    releaseFinger() {
      finger = null
    },
    destroy() {
      if (typeTimer !== null) window.clearTimeout(typeTimer)
      if (pending !== null) window.clearTimeout(pending)
    },
  }
}
