// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SCREEN_TEXT_MAX_BOXES, type ScreenText, type TextBox } from './protocol.generated'
import type { FramePoint } from './screen-canvas'
import { CAPTION_FLIP_SHARE, LOW_CONFIDENCE, createTextOverlay, readScreenText } from './text-overlay'

const BOX = { x: 10, y: 20, w: 400, h: 800 }
const SIGN_IN: TextBox = { text: 'Sign in', confidence: 0.93, x: 0.1, y: 0.4, w: 0.3, h: 0.05 }

function reading(boxes: TextBox[] = [SIGN_IN], hold_ms = 0): ScreenText {
  return { type: 'screen_text', id: 't1', hold_ms, boxes }
}

/** A pointer at a share of the screen: clientX and clientY are read as thousandths of it. */
function at(event: MouseEvent): FramePoint {
  const nx = event.clientX / 1000
  const ny = event.clientY / 1000
  return { x: nx * 402, y: ny * 874, nx, ny, inside: nx >= 0 && nx <= 1 && ny >= 0 && ny <= 1 }
}

function move(target: HTMLElement, nx: number, ny: number, buttons = 0): void {
  target.dispatchEvent(new MouseEvent('pointermove', { clientX: nx * 1000, clientY: ny * 1000, buttons }))
}

function setup(options: { pointer?: boolean } = {}) {
  const overlay = document.createElement('div')
  const canvas = document.createElement('canvas')
  document.body.append(overlay, canvas)
  const frameBox = vi.fn(() => BOX)
  const text = createTextOverlay(overlay, {
    frameBox, pointer: options.pointer === false ? undefined : { target: canvas, at },
  })
  const $ = <T extends Element>(selector: string) => text.el.querySelector<T>(selector)!
  const boxes = () => Array.from(text.el.querySelectorAll<HTMLElement>('[data-tx-box]'))
  return { overlay, canvas, frameBox, text, $, boxes, caption: $<HTMLElement>('[data-tx-caption]') }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('readScreenText', () => {
  it('reads a message and leaves out what it does not know and boxes past the most one holds', () => {
    expect(readScreenText({ ...reading(), extra: 1 })).toEqual(reading())
    const many = readScreenText(reading(Array.from({ length: SCREEN_TEXT_MAX_BOXES + 3 }, () => SIGN_IN)))
    expect(many?.boxes).toHaveLength(SCREEN_TEXT_MAX_BOXES)
    expect(readScreenText({ ...reading(), boxes: [{ ...SIGN_IN, extra: true }] })?.boxes).toEqual([SIGN_IN])
  })

  it.each([
    null, 'screen_text', 7,
    { ...reading(), type: 'agent' },
    { ...reading(), id: 1 },
    { ...reading(), hold_ms: -1 },
    { ...reading(), hold_ms: 1.5 },
    { ...reading(), hold_ms: undefined },
    { ...reading(), boxes: 'none' },
    reading([null as unknown as TextBox]),
    reading([{ ...SIGN_IN, text: 3 as unknown as string }]),
    reading([{ ...SIGN_IN, confidence: 1.2 }]),
    reading([{ ...SIGN_IN, confidence: undefined as unknown as number }]),
    reading([{ ...SIGN_IN, x: -0.1 }]),
    reading([{ ...SIGN_IN, y: 1.5 }]),
    reading([{ ...SIGN_IN, w: '0' as unknown as number }]),
    reading([{ ...SIGN_IN, h: Number.NaN }]),
  ])('refuses what is not a screen_text message: %j', (raw) => {
    expect(readScreenText(raw)).toBeNull()
  })
})

describe('createTextOverlay', () => {
  it('outlines each line where it is on the screen, dashed where the reading was unsure, and hidden from readers', () => {
    const { text, $, boxes } = setup()
    expect(text.el.hidden).toBe(true)
    expect(text.el.getAttribute('aria-hidden')).toBe('true')
    text.show(reading([SIGN_IN, { ...SIGN_IN, text: 'Terms', confidence: LOW_CONFIDENCE - 0.01 },
                       { ...SIGN_IN, text: 'Help', confidence: LOW_CONFIDENCE }]))
    expect(text.el.hidden).toBe(false)
    expect(text.count).toBe(3)
    const frame = $<HTMLElement>('[data-tx-frame]')
    expect([frame.style.transform, frame.style.width, frame.style.height]).toEqual(['translate(10px, 20px)', '400px', '800px'])
    const [first, faint, sure] = boxes()
    expect([first.style.left, first.style.top, first.style.width, first.style.height]).toEqual(['10%', '40%', '30%', '5%'])
    expect(faint.classList.contains('is-faint')).toBe(true)
    expect(sure.classList.contains('is-faint')).toBe(false)
  })

  it('replaces the boxes with each reading, and one with none clears them', () => {
    const { text, boxes } = setup()
    text.show(reading([SIGN_IN, SIGN_IN]))
    text.show(reading([{ ...SIGN_IN, text: 'Welcome' }]))
    expect(boxes()).toHaveLength(1)
    text.show(reading([]))
    expect(text.count).toBe(0)
    expect(boxes()).toHaveLength(0)
    expect(text.el.hidden).toBe(true)
  })

  it('lets boxes nobody cleared go after their hold, and a new reading or a clear starts the hold again', () => {
    const { text } = setup()
    text.show(reading([SIGN_IN], 1000))
    vi.advanceTimersByTime(600)
    text.show(reading([SIGN_IN], 1000))
    vi.advanceTimersByTime(600)
    expect(text.count).toBe(1)
    vi.advanceTimersByTime(400)
    expect(text.count).toBe(0)
    text.show(reading([SIGN_IN], 0))
    vi.advanceTimersByTime(3_600_000)
    expect(text.count).toBe(1)
    text.show(reading([SIGN_IN], 500))
    text.clear()
    text.show(reading([SIGN_IN], 0))
    vi.advanceTimersByTime(1000)
    expect(text.count).toBe(1)
  })

  it('measures the screen again when the overlay changes size, while boxes are drawn', () => {
    let resized: () => void = () => {}
    const observe = vi.fn()
    const disconnect = vi.fn()
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) {
        resized = callback
      }

      observe = observe
      disconnect = disconnect
    })
    const { text, frameBox, overlay } = setup()
    expect(observe).toHaveBeenCalledWith(overlay)
    resized()
    expect(frameBox).not.toHaveBeenCalled()
    text.show(reading())
    frameBox.mockReturnValue({ x: 0, y: 0, w: 200, h: 400 })
    resized()
    expect(text.el.querySelector<HTMLElement>('[data-tx-frame]')!.style.width).toBe('200px')
    text.layout()
    expect(frameBox).toHaveBeenCalledTimes(3)
    text.destroy()
    expect(disconnect).toHaveBeenCalled()
  })

  it('says what the box under the pointer reads, the smallest of nested ones, and how sure', () => {
    const { text, canvas, caption, boxes } = setup()
    move(canvas, 0.2, 0.42)
    expect(caption.hidden).toBe(true)
    const row: TextBox = { text: 'Account settings and privacy', confidence: 0.61, x: 0.25, y: 0.38, w: 0.75, h: 0.1 }
    text.show(reading([row, SIGN_IN]))
    move(canvas, 0.3, 0.42)
    expect(caption.hidden).toBe(false)
    expect(caption.textContent).toBe('Sign in93%')
    expect(boxes()[1].classList.contains('is-pointed')).toBe(true)
    expect([caption.style.left, caption.style.top]).toEqual(['10%', '40%'])
    expect(caption.className).toBe('tx-caption')
    move(canvas, 0.8, 0.46)
    expect(caption.textContent).toBe('Account settings and privacy61%')
    expect(boxes()[1].classList.contains('is-pointed')).toBe(false)
    expect(boxes()[0].classList.contains('is-pointed')).toBe(true)
    expect(caption.classList.contains('is-end')).toBe(true)
    expect(caption.style.left).toBe('100%')
  })

  it('says a box near the top below it, and says nothing while pressed, off the boxes, or once the pointer leaves', () => {
    const { text, canvas, caption, boxes } = setup()
    const top: TextBox = { ...SIGN_IN, y: CAPTION_FLIP_SHARE - 0.05 }
    text.show(reading([top]))
    move(canvas, 0.2, top.y + 0.01)
    expect(caption.classList.contains('is-below')).toBe(true)
    expect(caption.style.top).toBe(`${(top.y + top.h) * 100}%`)
    move(canvas, 0.2, top.y + 0.01, 1)
    expect(caption.hidden).toBe(true)
    move(canvas, 0.9, 0.9)
    expect(caption.hidden).toBe(true)
    move(canvas, 1.5, 0.05)
    expect(caption.hidden).toBe(true)
    move(canvas, 0.2, top.y + 0.01)
    canvas.dispatchEvent(new MouseEvent('pointerleave'))
    expect(caption.hidden).toBe(true)
    expect(boxes()[0].classList.contains('is-pointed')).toBe(false)
    move(canvas, 0.2, top.y + 0.01)
    text.clear()
    expect(caption.hidden).toBe(true)
  })

  it('writes what was read as text, never as markup', () => {
    const { text, canvas, caption } = setup()
    text.show(reading([{ ...SIGN_IN, text: '<img src=x onerror="alert(1)">' }]))
    move(canvas, 0.2, 0.42)
    expect(caption.querySelector('img')).toBeNull()
    expect(caption.textContent).toContain('<img src=x')
  })

  it('draws without a pointer to follow or a way to watch the overlay resize', () => {
    const { text, canvas, caption } = setup({ pointer: false })
    text.show(reading())
    move(canvas, 0.2, 0.42)
    expect(text.count).toBe(1)
    expect(caption.hidden).toBe(true)
    text.destroy()
  })

  it('lets go of everything when destroyed', () => {
    const { text, canvas, overlay } = setup()
    text.show(reading([SIGN_IN], 1000))
    text.destroy()
    expect(overlay.querySelector('.tx')).toBeNull()
    text.show(reading())
    move(canvas, 0.2, 0.42)
    vi.advanceTimersByTime(2000)
    expect(text.count).toBe(0)
  })
})
