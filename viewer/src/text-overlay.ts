// SPDX-License-Identifier: Apache-2.0
/**
 * The text a reading of the screen's pixels found, outlined over the screen by the viewer.
 *
 * While a scope's ``perception.ocr_overlay`` is on, the server sends a ``screen_text`` message after each reading of
 * the screen's pixels: every line of text it kept, and the box around it as shares of the screen. Each message replaces
 * the boxes drawn before, and one with no boxes clears them -- the server sends that when the screen is about to
 * change. A box is dashed when the reading was unsure of it, and pointing at one says what it reads and how sure the
 * reading was. Boxes nobody cleared go after the message's `hold_ms`.
 *
 * The boxes are drawn here, over the screen, never into it: a screenshot or a recording of the device does not show
 * them, and a press goes through them to the screen underneath.
 */
import { SCREEN_TEXT_MAX_BOXES, type ScreenText, type TextBox } from './protocol.generated'
import type { FramePoint, Rect } from './screen-canvas'

/** Below this a box is drawn dashed: the reading was unsure of it. */
export const LOW_CONFIDENCE = 0.5
/** A box this near the top says its text below it, where there is room. */
export const CAPTION_FLIP_SHARE = 0.08

export interface TextOverlayOptions {
  /** Where the device's screen is drawn inside the overlay, in CSS pixels. */
  frameBox(): Rect
  /** Where a pointer is on the screen, so the box under it can say its text; no captions without it. */
  pointer?: { target: HTMLElement; at(event: MouseEvent): FramePoint }
}

export interface TextOverlayHandle {
  readonly el: HTMLElement
  /** How many boxes are drawn now. */
  readonly count: number
  /** Draw a reading's boxes in place of the last; none clears them. */
  show(text: ScreenText): void
  clear(): void
  /** Measure the screen again; done by itself when the overlay changes size. */
  layout(): void
  destroy(): void
}

const isShare = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
const isWhole = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0

function readBox(raw: unknown): TextBox | null {
  if (!raw || typeof raw !== 'object') return null
  const box = raw as Record<string, unknown>
  if (typeof box.text !== 'string' || !isShare(box.confidence) || !isShare(box.x) || !isShare(box.y)
      || !isShare(box.w) || !isShare(box.h)) return null
  return { text: box.text, confidence: box.confidence, x: box.x, y: box.y, w: box.w, h: box.h }
}

/** A screen_text message from the socket, checked -- boxes past the most a message holds left out -- or null. */
export function readScreenText(raw: unknown): ScreenText | null {
  if (!raw || typeof raw !== 'object') return null
  const body = raw as Record<string, unknown>
  if (body.type !== 'screen_text' || typeof body.id !== 'string' || !isWhole(body.hold_ms)
      || !Array.isArray(body.boxes)) return null
  const boxes = body.boxes.slice(0, SCREEN_TEXT_MAX_BOXES).map(readBox)
  if (boxes.some((box) => box === null)) return null
  return { type: 'screen_text', id: body.id, hold_ms: body.hold_ms, boxes: boxes as TextBox[] }
}

const percent = (share: number): string => `${share * 100}%`

export function createTextOverlay(overlay: HTMLElement, options: TextOverlayOptions): TextOverlayHandle {
  const el = document.createElement('div')
  el.className = 'tx'
  el.hidden = true
  el.setAttribute('aria-hidden', 'true')
  const frame = document.createElement('div')
  frame.className = 'tx-frame'
  frame.dataset.txFrame = ''
  const caption = document.createElement('div')
  caption.className = 'tx-caption'
  caption.dataset.txCaption = ''
  caption.hidden = true
  frame.appendChild(caption)
  el.appendChild(frame)
  overlay.appendChild(el)

  let boxes: TextBox[] = []
  let drawn: HTMLElement[] = []
  let pointed: HTMLElement | null = null
  let hold: number | null = null
  let destroyed = false

  function layout(): void {
    const box = options.frameBox()
    frame.style.transform = `translate(${box.x}px, ${box.y}px)`
    frame.style.width = `${box.w}px`
    frame.style.height = `${box.h}px`
  }

  function letGoOfHold(): void {
    if (hold !== null) window.clearTimeout(hold)
    hold = null
  }

  function unpoint(): void {
    pointed?.classList.remove('is-pointed')
    pointed = null
    caption.hidden = true
  }

  function clear(): void {
    letGoOfHold()
    unpoint()
    boxes = []
    drawn = []
    frame.replaceChildren(caption)
    el.hidden = true
  }

  function show(text: ScreenText): void {
    if (destroyed) return
    if (text.boxes.length === 0) return clear()
    letGoOfHold()
    unpoint()
    boxes = text.boxes
    drawn = boxes.map((box) => {
      const outline = document.createElement('div')
      outline.className = box.confidence < LOW_CONFIDENCE ? 'tx-box is-faint' : 'tx-box'
      outline.dataset.txBox = ''
      Object.assign(outline.style, { left: percent(box.x), top: percent(box.y), width: percent(box.w),
                                     height: percent(box.h) })
      return outline
    })
    frame.replaceChildren(...drawn, caption)
    el.hidden = false
    layout()
    if (text.hold_ms > 0) hold = window.setTimeout(clear, text.hold_ms)
  }

  /** The smallest box under a point on the screen: the one a person means when boxes are nested. */
  function boxAt(at: FramePoint): number {
    let found = -1
    let area = Infinity
    boxes.forEach((box, index) => {
      const over = at.nx >= box.x && at.nx <= box.x + box.w && at.ny >= box.y && at.ny <= box.y + box.h
      if (over && box.w * box.h < area) {
        found = index
        area = box.w * box.h
      }
    })
    return found
  }

  function pointAt(event: MouseEvent): void {
    // A press is a person using the screen, not reading what is on it.
    const at = boxes.length > 0 && event.buttons === 0 ? options.pointer!.at(event) : null
    const index = at?.inside ? boxAt(at) : -1
    if (index < 0) return unpoint()
    const box = boxes[index]
    pointed?.classList.remove('is-pointed')
    pointed = drawn[index]
    pointed.classList.add('is-pointed')
    const said = document.createElement('span')
    said.className = 'tx-said'
    said.textContent = box.text
    const sure = document.createElement('span')
    sure.className = 'tx-sure'
    sure.textContent = `${Math.round(box.confidence * 100)}%`
    caption.replaceChildren(said, sure)
    const below = box.y < CAPTION_FLIP_SHARE
    const end = box.x + box.w / 2 > 0.5
    caption.classList.toggle('is-below', below)
    caption.classList.toggle('is-end', end)
    caption.style.left = percent(end ? box.x + box.w : box.x)
    caption.style.top = percent(below ? box.y + box.h : box.y)
    caption.hidden = false
  }

  const target = options.pointer?.target
  target?.addEventListener('pointermove', pointAt)
  target?.addEventListener('pointerleave', unpoint)
  const resized = typeof ResizeObserver === 'function'
    ? new ResizeObserver(() => {
      if (boxes.length > 0) layout()
    })
    : null
  resized?.observe(overlay)

  return {
    el,
    get count() {
      return boxes.length
    },
    show,
    clear,
    layout,
    destroy() {
      destroyed = true
      clear()
      resized?.disconnect()
      target?.removeEventListener('pointermove', pointAt)
      target?.removeEventListener('pointerleave', unpoint)
      el.remove()
    },
  }
}
