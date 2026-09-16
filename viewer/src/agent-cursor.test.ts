// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ACTIVE_MS, CAPTION_MIN_MS, FAILED_MS, GLIDE_MAX_MS, REST_MS, RIPPLE_MS, createAgentCursor, readAgentEvent,
} from './agent-cursor'
import type { AgentIntent, AgentWorking } from './protocol.generated'

const BOX = { x: 10, y: 20, w: 400, h: 800 }

function intent(overrides: Partial<AgentIntent> & { kind?: string; points?: Array<[number, number]>;
                                                  duration_ms?: number } = {}): AgentIntent {
  const { kind = 'tap', points = [[0.5, 0.25]], duration_ms = 80, ...rest } = overrides
  return {
    type: 'agent', id: 'a1', phase: 'intent', agent: { key: 'agent-1', title: 'Claude Code · notes' },
    gesture: { kind, duration_ms, points }, label: '', caption: '', lead_ms: 250, linger_ms: 0, pointer: true, ...rest,
  }
}

function working(linger_ms: number, title = 'Claude Code · notes'): AgentWorking {
  return { type: 'agent', id: 'w1', phase: 'working', agent: { key: 'agent-1', title }, linger_ms }
}

const done = (ok = true) => ({ type: 'agent', id: 'a1', phase: 'done', ok }) as const

function setup(options: { reduced?: boolean; activeMs?: number } = {}) {
  const overlay = document.createElement('div')
  document.body.appendChild(overlay)
  const onActive = vi.fn()
  const cursor = createAgentCursor(overlay, {
    frameBox: () => BOX, reducedMotion: () => options.reduced ?? false, activeMs: options.activeMs, onActive,
  })
  const $ = <T extends Element>(selector: string) => cursor.el.querySelector<T>(selector)
  return { overlay, cursor, onActive, $, pointer: $<HTMLElement>('[data-ac-pointer]')! }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('readAgentEvent', () => {
  it('reads an intent and a done, filling what an intent may leave out', () => {
    const raw = { ...intent({ label: 'General', caption: 'tap "General"' }), extra: 1 }
    expect(readAgentEvent(raw)).toEqual(intent({ label: 'General', caption: 'tap "General"' }))
    const bare = { ...intent(), label: undefined, caption: 3 }
    expect(readAgentEvent(bare)).toMatchObject({ label: '', caption: '', pointer: true })
    expect(readAgentEvent({ ...intent(), pointer: false })).toMatchObject({ pointer: false })
    expect(readAgentEvent({ type: 'agent', id: 'a1', phase: 'done', ok: false }))
      .toEqual({ type: 'agent', id: 'a1', phase: 'done', ok: false })
  })

  it('reads how long the pointer lingers, and an agent still working -- none from an older server lingers', () => {
    expect(readAgentEvent({ ...intent(), linger_ms: 60_000 })).toMatchObject({ linger_ms: 60_000 })
    expect(readAgentEvent({ ...intent(), linger_ms: undefined })).toMatchObject({ linger_ms: 0 })
    expect(readAgentEvent({ ...intent(), linger_ms: -5 })).toMatchObject({ linger_ms: 0 })
    expect(readAgentEvent({ ...working(90_000), extra: true })).toEqual(working(90_000))
    expect(readAgentEvent({ ...working(90_000), linger_ms: 'long' })).toEqual(working(0))
    expect(readAgentEvent({ ...working(1), agent: { key: 'k' } })).toBeNull()
    expect(readAgentEvent({ ...working(1), agent: null })).toBeNull()
  })

  it('refuses anything that is not an agent event it can draw', () => {
    const good = intent()
    const refused: unknown[] = [
      null, 'agent', { type: 'status' }, { type: 'agent', phase: 'done' }, { type: 'agent', id: 'a', phase: 'done' },
      { ...good, phase: 'other' }, { ...good, agent: null }, { ...good, agent: { key: 'k' } },
      { ...good, gesture: null }, { ...good, gesture: { ...good.gesture, kind: 1 } },
      { ...good, gesture: { ...good.gesture, duration_ms: -1 } },
      { ...good, gesture: { ...good.gesture, points: 'no' } },
      { ...good, gesture: { ...good.gesture, points: [[0.5]] } },
      { ...good, gesture: { ...good.gesture, points: [[1.5, 0]] } },
      { ...good, gesture: { ...good.gesture, points: [['0', 0]] } },
      { ...good, lead_ms: Number.NaN },
    ]
    for (const raw of refused) expect(readAgentEvent(raw)).toBeNull()
  })
})

describe('createAgentCursor', () => {
  it('glides to a tap, ripples when it lands, and names the agent', () => {
    const { cursor, pointer, onActive, $ } = setup()
    expect(cursor.el.hidden).toBe(true)
    expect($('.ac-arrow svg[data-icon="pointer"]')).not.toBeNull()
    cursor.handle(intent())
    expect(cursor.el.hidden).toBe(false)
    expect(pointer.style.transform).toBe('translate(210px, 220px)')
    expect(pointer.style.transitionDuration).toBe(`250ms, ${REST_MS}ms`)
    expect($('[data-ac-chip]')!.textContent).toBe('Claude Code · notes')
    expect(onActive).toHaveBeenCalledWith(true, 'Claude Code · notes')
    expect($('.ac-ripple')).toBeNull()
    vi.advanceTimersByTime(250)
    expect($<HTMLElement>('.ac-ripple')!.style.transform).toBe('translate(210px, 220px)')
    vi.advanceTimersByTime(RIPPLE_MS)
    expect($('.ac-ripple')).toBeNull()
  })

  it('draws its pointer with a host’s own icon when given one', () => {
    const overlay = document.createElement('div')
    const cursor = createAgentCursor(overlay, { frameBox: () => BOX, icon: (name) => `<i data-host-icon="${name}"></i>` })
    expect(cursor.el.querySelector('[data-host-icon="pointer"]')).not.toBeNull()
  })

  it('glides no longer than the most it may, and jumps with reduced motion', () => {
    const slow = setup()
    slow.cursor.handle(intent({ lead_ms: 1000 }))
    expect(slow.pointer.style.transitionDuration).toBe(`${GLIDE_MAX_MS}ms, ${REST_MS}ms`)
    const still = setup({ reduced: true })
    still.cursor.handle(intent())
    expect(still.pointer.style.transitionDuration).toBe('0ms, 0ms')
  })

  it('follows the system setting for motion when not told', () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
    const overlay = document.createElement('div')
    const cursor = createAgentCursor(overlay, { frameBox: () => BOX })
    cursor.handle(intent())
    expect(cursor.el.querySelector<HTMLElement>('[data-ac-pointer]')!.style.transitionDuration).toBe('0ms, 0ms')
    vi.stubGlobal('matchMedia', undefined)
    const other = createAgentCursor(overlay, { frameBox: () => BOX })
    other.handle(intent())
    expect(other.el.querySelector<HTMLElement>('[data-ac-pointer]')!.style.transitionDuration).toBe(`250ms, ${REST_MS}ms`)
    other.handle({ type: 'agent', id: 'a1', phase: 'done', ok: true })
  })

  it('fills a ring for as long as a press holds', () => {
    const { cursor, $ } = setup()
    cursor.handle(intent({ kind: 'long_press', duration_ms: 800 }))
    vi.advanceTimersByTime(250)
    expect($<HTMLElement>('.ac-hold')!.style.animationDuration).toBe('800ms')
    vi.advanceTimersByTime(800 + RIPPLE_MS)
    expect($('.ac-hold')).toBeNull()
  })

  it('draws a swipe as a trail the pointer follows', () => {
    const { cursor, pointer, $ } = setup()
    cursor.handle(intent({ kind: 'swipe', duration_ms: 300, points: [[0, 0], [0.5, 0.5], [1, 1]] }))
    expect($('.ac-trail polyline')!.getAttribute('points')).toBe('10,20 210,420 410,820')
    vi.advanceTimersByTime(250)
    expect(pointer.style.transform).toBe('translate(410px, 820px)')
    expect(pointer.style.transitionDuration).toBe(`300ms, ${REST_MS}ms`)
    vi.advanceTimersByTime(300 + RIPPLE_MS)
    expect($('.ac-trail')).toBeNull()
  })

  it('captions typing where it types, an app along the top, and draws nothing for a bare step', () => {
    const { cursor, $ } = setup()
    cursor.handle(intent({ kind: 'type', caption: 'Trip', duration_ms: 2000 }))
    const typed = $<HTMLElement>('.ac-caption')!
    expect(typed.textContent).toBe('Trip')
    expect(typed.style.transform).toBe('translate(210px, 220px)')
    vi.advanceTimersByTime(2250)
    expect($('.ac-caption')).toBeNull()

    cursor.handle(intent({ kind: 'app', points: [], label: 'launch com.acme.Notes', duration_ms: 0 }))
    const top = $<HTMLElement>('.ac-caption-top')!
    expect(top.textContent).toBe('launch com.acme.Notes')
    expect(top.style.transform).toBe('')
    vi.advanceTimersByTime(CAPTION_MIN_MS)
    expect($('.ac-caption-top')).toBeNull()

    const before = cursor.el.children.length
    cursor.handle(intent({ kind: 'pause', points: [] }))
    cursor.handle(intent({ kind: 'press' }))
    cursor.handle(intent({ kind: 'drag', points: [[0.1, 0.1]] }))
    expect(cursor.el.children.length).toBe(before)
  })

  it('shakes on a failure and stays still on a success', () => {
    const { cursor, pointer } = setup()
    cursor.handle({ type: 'agent', id: 'a1', phase: 'done', ok: true })
    expect(pointer.classList.contains('is-failed')).toBe(false)
    cursor.handle({ type: 'agent', id: 'a2', phase: 'done', ok: false })
    expect(pointer.classList.contains('is-failed')).toBe(true)
    vi.advanceTimersByTime(FAILED_MS)
    expect(pointer.classList.contains('is-failed')).toBe(false)
  })

  it('counts as using the device until its last event is old, and says when that ends', () => {
    const { cursor, onActive } = setup()
    cursor.handle(intent())
    vi.advanceTimersByTime(ACTIVE_MS - 1)
    cursor.handle({ type: 'agent', id: 'a1', phase: 'done', ok: true })
    expect(onActive).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(ACTIVE_MS - 1)
    expect(cursor.active).toBe(true)
    vi.advanceTimersByTime(1)
    expect(cursor.active).toBe(false)
    expect(cursor.el.hidden).toBe(true)
    expect(onActive).toHaveBeenLastCalledWith(false, null)
    const quiet = createAgentCursor(document.createElement('div'), { frameBox: () => BOX, reducedMotion: () => false })
    quiet.handle(intent())
    vi.advanceTimersByTime(ACTIVE_MS)
    expect(quiet.active).toBe(false)
  })

  it('with the pointer off, says an agent is using the device and draws nothing', () => {
    const { cursor, onActive } = setup()
    cursor.handle(intent({ pointer: false, points: [], lead_ms: 0 }))
    expect(onActive).toHaveBeenCalledWith(true, 'Claude Code · notes')
    expect(cursor.active).toBe(true)
    expect(cursor.el.hidden).toBe(true)
    cursor.handle({ type: 'agent', id: 'a1', phase: 'done', ok: true })
    vi.advanceTimersByTime(RIPPLE_MS + CAPTION_MIN_MS)
    expect(cursor.el.hidden).toBe(true)
    expect(cursor.el.querySelectorAll('.ac-ripple, .ac-caption, .ac-trail, .ac-hold')).toHaveLength(0)
    vi.advanceTimersByTime(ACTIVE_MS)
    expect(onActive).toHaveBeenLastCalledWith(false, null)
  })

  it('shows the pointer only once a gesture places it, and hides it again when the agent is done', () => {
    const { cursor, pointer } = setup()
    expect(pointer.hidden).toBe(true)
    cursor.handle(intent({ kind: 'app', points: [], label: 'launch com.acme.Notes', duration_ms: 0 }))
    expect(cursor.el.hidden).toBe(false)
    expect(pointer.hidden).toBe(true)
    cursor.handle(intent())
    expect(pointer.hidden).toBe(false)
    vi.advanceTimersByTime(ACTIVE_MS)
    expect(cursor.el.hidden).toBe(true)
    expect(pointer.hidden).toBe(true)
  })

  it('rests the pointer where it landed, dimmed, for as long as the agent lingers after its last event', () => {
    const { cursor, pointer, onActive } = setup()
    cursor.handle(intent({ linger_ms: 60_000 }))
    expect(pointer.classList.contains('is-resting')).toBe(false)
    expect(pointer.style.transitionDuration).toBe(`250ms, ${REST_MS}ms`)
    cursor.handle(done())
    expect(pointer.classList.contains('is-resting')).toBe(true)
    vi.advanceTimersByTime(59_999)
    expect([cursor.el.hidden, pointer.hidden, cursor.active]).toEqual([false, false, true])
    cursor.handle(intent({ linger_ms: 60_000, points: [[0.1, 0.1]] }))
    expect(pointer.classList.contains('is-resting')).toBe(false)
    expect(pointer.style.transform).toBe('translate(50px, 100px)')
    cursor.handle(done())
    vi.advanceTimersByTime(60_000)
    expect([cursor.el.hidden, pointer.hidden, cursor.active]).toEqual([true, true, false])
    expect(onActive).toHaveBeenCalledTimes(2)
  })

  it('keeps the pointer through a long call the agent says it is still working on, and lets it go once quiet', () => {
    const { cursor, pointer } = setup()
    cursor.handle(intent({ linger_ms: 20_000 }))
    cursor.handle(done())
    for (let beat = 0; beat < 6; beat += 1) {
      vi.advanceTimersByTime(10_000)
      cursor.handle(working(20_000))
      expect([cursor.el.hidden, pointer.hidden, pointer.classList.contains('is-resting')]).toEqual([false, false, true])
    }
    vi.advanceTimersByTime(20_000)
    expect([cursor.el.hidden, pointer.hidden, cursor.active]).toEqual([true, true, false])
    // The agent comes back after thinking longer than it lingers: the pointer rests where it last landed again.
    cursor.handle(working(20_000, 'Claude Code · again'))
    expect([cursor.el.hidden, pointer.hidden, cursor.active]).toEqual([false, false, true])
    expect(pointer.style.transform).toBe('translate(210px, 220px)')
    expect(pointer.style.transitionDuration).toBe(`0ms, ${REST_MS}ms`)
    expect(cursor.el.querySelector('[data-ac-chip]')?.textContent).toBe('Claude Code · again')
  })

  it('says an agent is working without drawing a pointer no gesture placed, or one that is not to linger', () => {
    const { cursor, pointer, onActive } = setup()
    cursor.handle(working(60_000))
    expect([cursor.active, cursor.el.hidden, pointer.hidden]).toEqual([true, true, true])
    expect(onActive).toHaveBeenCalledWith(true, 'Claude Code · notes')
    cursor.handle(intent())
    cursor.handle(done())
    vi.advanceTimersByTime(ACTIVE_MS - 1)
    cursor.handle(working(0))
    vi.advanceTimersByTime(1)
    expect([cursor.active, pointer.hidden]).toEqual([true, true])
    vi.advanceTimersByTime(ACTIVE_MS)
    expect(cursor.active).toBe(false)
  })

  it('never draws into the screen: the pointer is the overlay\'s, over the canvas, not in it', () => {
    const stage = document.createElement('div')
    const canvas = document.createElement('canvas')
    const overlay = document.createElement('div')
    stage.append(canvas, overlay)
    const cursor = createAgentCursor(overlay, { frameBox: () => BOX, reducedMotion: () => true })
    cursor.handle(intent({ linger_ms: 60_000 }))
    cursor.handle(working(60_000))
    expect(overlay.contains(cursor.el)).toBe(true)
    expect(canvas.childNodes).toHaveLength(0)
    expect(cursor.el.getAttribute('aria-hidden')).toBe('true')
    expect(cursor.el.querySelector<HTMLElement>('[data-ac-pointer]')!.style.transitionDuration).toBe('0ms, 0ms')
  })

  it('lets go of everything on destroy', () => {
    const { cursor, overlay, onActive } = setup({ activeMs: 100 })
    cursor.handle(intent())
    cursor.handle({ type: 'agent', id: 'a1', phase: 'done', ok: false })
    cursor.destroy()
    vi.advanceTimersByTime(10_000)
    expect(overlay.children).toHaveLength(0)
    expect(onActive).toHaveBeenCalledTimes(1)
    const fresh = setup()
    fresh.cursor.destroy()
    const resting = setup()
    resting.cursor.handle(working(1000))
    resting.cursor.destroy()
    vi.advanceTimersByTime(10_000)
    expect(resting.onActive).toHaveBeenCalledTimes(1)
  })
})
