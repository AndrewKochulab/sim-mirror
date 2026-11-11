// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ACTIVE_MS, FAILED_MS, GLIDE_MAX_MS, IDLE_LABEL, ONGOING_MS, PARK_AFTER_MS, PARK_GLIDE_MS, PARK_INSET, POINTER_PATH,
  PRESS_MS, REST_MS, RIPPLE_MS, createAgentCursor, pointerSvg, readAgentEvent, textOn,
} from './agent-cursor'
import { WORKING_EVERY_S, type AgentIntent, type AgentWorking } from './protocol.generated'

const BOX = { x: 10, y: 20, w: 400, h: 800 }

function intent(overrides: Partial<AgentIntent> & { kind?: string; points?: Array<[number, number]>;
                                                  duration_ms?: number } = {}): AgentIntent {
  const { kind = 'tap', points = [[0.5, 0.25]], duration_ms = 80, ...rest } = overrides
  return {
    type: 'agent', id: 'a1', phase: 'intent', agent: { key: 'agent-1', title: 'Claude Code · notes' },
    gesture: { kind, duration_ms, points }, label: '', caption: '', activity: '', color: '', lead_ms: 250, linger_ms: 0,
    pointer: true, ...rest,
  }
}

function working(linger_ms: number, title = 'Claude Code · notes', ongoing = false, activity = '',
                 color = ''): AgentWorking {
  return {
    type: 'agent', id: 'w1', phase: 'working', agent: { key: 'agent-1', title }, ongoing, linger_ms, activity, color,
  }
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
    const bare = { ...intent(), label: undefined, caption: 3, activity: undefined }
    expect(readAgentEvent(bare)).toMatchObject({ label: '', caption: '', activity: '', pointer: true })
    expect(readAgentEvent(intent({ activity: 'Tapping “Add”' }))).toMatchObject({ activity: 'Tapping “Add”' })
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
    expect(readAgentEvent(working(1, 'x', true))).toMatchObject({ ongoing: true })
    expect(readAgentEvent({ ...working(1, 'x', true), ongoing: 'yes' })).toMatchObject({ ongoing: false })
    expect(readAgentEvent(working(1, 'x', true, 'Building'))).toMatchObject({ activity: 'Building' })
    expect(readAgentEvent({ ...working(1), activity: 7 })).toMatchObject({ activity: '' })
    expect(readAgentEvent({ ...working(1), color: '#7C5CFF' })).toMatchObject({ color: '#7c5cff' })
    expect(readAgentEvent({ ...intent(), color: 'red' })).toMatchObject({ color: '' })
    expect(readAgentEvent({ ...intent(), color: undefined })).toMatchObject({ color: '' })
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
    expect($('.ac-arrow svg[data-ac-shape] path')!.getAttribute('d')).toBe(POINTER_PATH)
    cursor.handle(intent())
    expect(cursor.el.hidden).toBe(false)
    expect(pointer.style.transform).toBe('translate(210px, 220px)')
    expect(pointer.style.transitionDuration).toBe(`250ms, ${REST_MS}ms`)
    expect($('[data-ac-chip]')!.textContent).toBe('Claude Code · notes')
    expect(onActive).toHaveBeenCalledWith(true, 'Claude Code · notes', '', '')
    expect($('.ac-ripple')).toBeNull()
    vi.advanceTimersByTime(250)
    // Placed by its own left and top, never a transform: the scale it grows by would scale that too, and the ring
    // would sweep in from the overlay's corner and on past the tap.
    const ring = $<HTMLElement>('.ac-ripple')!
    expect([ring.style.left, ring.style.top, ring.style.transform]).toEqual(['210px', '220px', ''])
    expect(ring.classList.contains('is-still')).toBe(false)
    vi.advanceTimersByTime(RIPPLE_MS)
    expect($('.ac-ripple')).toBeNull()
  })

  it('rings a right click too, and with reduced motion a ring fades where it is', () => {
    const { cursor, $ } = setup({ reduced: true })
    cursor.handle(intent({ kind: 'right_click' }))
    vi.advanceTimersByTime(250)
    expect($('.ac-ripple')!.classList.contains('is-still')).toBe(true)
  })

  it('labels the pointer with what the agent is doing, and tells the viewer with its name', () => {
    const { cursor, onActive, $ } = setup()
    const chip = () => $('[data-ac-chip]')!.textContent
    cursor.handle(intent({ activity: 'Tapping “Add”', label: 'Add', linger_ms: 60_000 }))
    expect(chip()).toBe('Tapping “Add”')
    expect(onActive).toHaveBeenLastCalledWith(true, 'Claude Code · notes', 'Tapping “Add”', '')
    cursor.handle(done())
    // The call goes on: a wait, then a read, each says what it is; a beat that says nothing keeps what was said.
    cursor.handle(working(60_000, 'Claude Code · notes', true, 'Waiting for “Saved”'))
    expect(chip()).toBe('Waiting for “Saved”')
    cursor.handle(working(60_000, 'Claude Code · notes', true))
    expect(chip()).toBe('Waiting for “Saved”')
    const calls = onActive.mock.calls.length
    cursor.handle(working(60_000, 'Claude Code · notes', true))
    expect(onActive.mock.calls.length).toBe(calls)
    // The call ends: nothing more to say, so the pointer is labelled with the agent's name again.
    cursor.handle(working(60_000, 'Claude Code · notes', false))
    expect(chip()).toBe('Claude Code · notes')
    expect(onActive).toHaveBeenLastCalledWith(true, 'Claude Code · notes', '', '')
    // A server from before activities is shown what it lands on, or what it types.
    cursor.handle(intent({ label: 'General', linger_ms: 60_000 }))
    expect(chip()).toBe('General')
    cursor.handle(intent({ kind: 'type', caption: 'Trip', linger_ms: 60_000 }))
    expect(chip()).toBe('Trip')
  })

  it('parks an idle pointer in the corner, labelled idle, and brings it back for the next gesture', () => {
    const { cursor, pointer, $ } = setup()
    cursor.handle(intent({ linger_ms: 60_000, activity: 'Tapping “Add”' }))
    cursor.handle(done())
    cursor.handle(working(60_000, 'Claude Code · notes', false))
    vi.advanceTimersByTime(250 + 80 + PARK_AFTER_MS - 1)
    expect(pointer.classList.contains('is-parked')).toBe(false)
    vi.advanceTimersByTime(1)
    const corner = `translate(${BOX.x + BOX.w - PARK_INSET}px, ${BOX.y + BOX.h - PARK_INSET}px)`
    expect(pointer.style.transform).toBe(corner)
    expect(pointer.style.transitionDuration).toBe(`${PARK_GLIDE_MS}ms, ${REST_MS}ms`)
    expect([pointer.classList.contains('is-parked'), pointer.classList.contains('is-resting')]).toEqual([true, false])
    expect(pointer.classList.contains('is-flipped')).toBe(true)
    expect($('[data-ac-chip]')!.textContent).toBe(IDLE_LABEL)
    // Still working while parked: it stays in the corner, saying what it does; a success leaves it parked.
    cursor.handle(working(60_000, 'Claude Code · notes', true, 'Building'))
    expect([pointer.style.transform, $('[data-ac-chip]')!.textContent]).toEqual([corner, 'Building'])
    cursor.handle(done())
    expect(pointer.classList.contains('is-resting')).toBe(false)
    cursor.handle(intent({ linger_ms: 60_000, points: [[0.1, 0.1]] }))
    expect(pointer.classList.contains('is-parked')).toBe(false)
    expect(pointer.style.transform).toBe('translate(50px, 100px)')
    expect(pointer.style.transitionDuration).toBe(`250ms, ${REST_MS}ms`)
    // A pointer let go of is not parked when it comes back.
    vi.advanceTimersByTime(60_000)
    expect(pointer.hidden).toBe(true)
    expect(pointer.classList.contains('is-parked')).toBe(false)
  })

  it('jumps to the corner with reduced motion, and puts its label on its left near the right edge', () => {
    const { cursor, pointer } = setup({ reduced: true })
    cursor.handle(intent({ linger_ms: 60_000, points: [[0.95, 0.5]] }))
    expect(pointer.classList.contains('is-flipped')).toBe(true)
    cursor.handle(intent({ linger_ms: 60_000, points: [[0.1, 0.5]] }))
    expect(pointer.classList.contains('is-flipped')).toBe(false)
    vi.advanceTimersByTime(250 + 80 + PARK_AFTER_MS)
    expect(pointer.style.transitionDuration).toBe('0ms, 0ms')
    expect(pointer.classList.contains('is-parked')).toBe(true)
  })

  it('draws its own pointer, each with a gradient of its own so two on a page never share one', () => {
    const one = setup().$('[data-ac-shape] linearGradient')!.id
    const two = setup().$('[data-ac-shape] linearGradient')!.id
    expect(one).not.toBe(two)
    expect(pointerSvg()).toContain('fill="url(#smv-ac-fill-')
  })

  it('draws the agent in the scope’s own colour, with text that reads on it, and in the accent without one', () => {
    const { cursor, onActive } = setup()
    const style = () => [cursor.el.style.getPropertyValue('--ac-color'), cursor.el.style.getPropertyValue('--ac-text')]
    cursor.handle(intent({ color: '#7c5cff' }))
    expect(style()).toEqual(['#7c5cff', '#ffffff'])
    expect(onActive).toHaveBeenLastCalledWith(true, 'Claude Code · notes', '', '#7c5cff')
    cursor.handle(working(0, 'Claude Code · notes', true, '', '#ffd60a'))
    expect(style()).toEqual(['#ffd60a', '#111113'])
    cursor.handle(intent())
    expect(style()).toEqual(['', ''])
  })

  it('picks white text on a dark colour and near-black on a light one', () => {
    expect(['#0a84ff', '#7c5cff', '#000000', '#1c1c1e'].map(textOn)).toEqual(Array(4).fill('#ffffff'))
    expect(['#ffffff', '#ffd60a', '#30d158', '#64d2ff'].map(textOn)).toEqual(Array(4).fill('#111113'))
  })

  it('presses down as a tap lands, and lets go -- but not with reduced motion', () => {
    const { cursor, pointer } = setup()
    cursor.handle(intent())
    vi.advanceTimersByTime(250)
    expect(pointer.classList.contains('is-pressing')).toBe(true)
    vi.advanceTimersByTime(PRESS_MS)
    expect(pointer.classList.contains('is-pressing')).toBe(false)
    const still = setup({ reduced: true })
    still.cursor.handle(intent())
    vi.advanceTimersByTime(250)
    expect(still.pointer.classList.contains('is-pressing')).toBe(false)
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

  it('draws nothing more than the pointer for typing, an app, or a bare step', () => {
    const { cursor, $ } = setup()
    cursor.handle(intent({ kind: 'type', caption: 'Trip', duration_ms: 2000, activity: 'Typing “Trip”' }))
    expect($('[data-ac-chip]')!.textContent).toBe('Typing “Trip”')
    cursor.handle(intent({ kind: 'app', points: [], caption: 'launch com.acme.Notes', duration_ms: 0 }))
    expect($('[data-ac-chip]')!.textContent).toBe('launch com.acme.Notes')
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
    expect(onActive).toHaveBeenLastCalledWith(false, null, '', '')
    const quiet = createAgentCursor(document.createElement('div'), { frameBox: () => BOX, reducedMotion: () => false })
    quiet.handle(intent())
    vi.advanceTimersByTime(ACTIVE_MS)
    expect(quiet.active).toBe(false)
  })

  it('with the pointer off, says an agent is using the device and draws nothing', () => {
    const { cursor, onActive } = setup()
    cursor.handle(intent({ pointer: false, points: [], lead_ms: 0, activity: 'Tapping' }))
    expect(onActive).toHaveBeenCalledWith(true, 'Claude Code · notes', 'Tapping', '')
    expect(cursor.active).toBe(true)
    expect(cursor.el.hidden).toBe(true)
    cursor.handle({ type: 'agent', id: 'a1', phase: 'done', ok: true })
    vi.advanceTimersByTime(RIPPLE_MS)
    expect(cursor.el.hidden).toBe(true)
    expect(cursor.el.querySelectorAll('.ac-ripple, .ac-trail, .ac-hold')).toHaveLength(0)
    vi.advanceTimersByTime(ACTIVE_MS)
    expect(onActive).toHaveBeenLastCalledWith(false, null, '', '')
  })

  it('shows the pointer only once a gesture places it, and hides it again when the agent is done', () => {
    const { cursor, pointer } = setup()
    expect(pointer.hidden).toBe(true)
    cursor.handle(intent({ kind: 'app', points: [], label: 'launch com.acme.Notes', duration_ms: 0 }))
    expect(cursor.el.hidden).toBe(true)
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
      // Idle for longer than it takes to park: waiting in the corner, not over the app.
      expect([cursor.el.hidden, pointer.hidden, pointer.classList.contains('is-parked')]).toEqual([false, false, true])
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

  it('holds the pointer through a call still running, however short it lingers, and lets it linger once it ends', () => {
    expect(ONGOING_MS).toBe(WORKING_EVERY_S * 1500)
    const { cursor, pointer } = setup()
    cursor.handle(working(1000, 'Claude Code · notes', true))
    cursor.handle(intent({ linger_ms: 1000 }))
    vi.advanceTimersByTime(2000)
    cursor.handle(done())
    // A tap mid-call, then the build it started goes quiet: the call's beats hold the pointer, not its one second.
    for (let beat = 0; beat < 5; beat += 1) {
      vi.advanceTimersByTime(WORKING_EVERY_S * 1000 - 1)
      expect([pointer.hidden, cursor.active]).toEqual([false, true])
      cursor.handle(working(1000, 'Claude Code · notes', true))
    }
    vi.advanceTimersByTime(ONGOING_MS - 1)
    expect(pointer.hidden).toBe(false)
    cursor.handle(working(1000, 'Claude Code · notes', false))
    vi.advanceTimersByTime(ACTIVE_MS - 1)
    expect([pointer.hidden, cursor.active]).toEqual([false, true])
    vi.advanceTimersByTime(1)
    expect([pointer.hidden, cursor.active]).toEqual([true, false])
  })

  it('lets a call that stopped beating go, once past when its next beat was due', () => {
    const { cursor, pointer } = setup()
    cursor.handle(intent({ linger_ms: 1000 }))
    cursor.handle(working(1000, 'Claude Code · notes', true))
    vi.advanceTimersByTime(ONGOING_MS - 1)
    expect(pointer.hidden).toBe(false)
    vi.advanceTimersByTime(1)
    expect([pointer.hidden, cursor.active]).toEqual([true, false])
  })

  it('says an agent is working without drawing a pointer no gesture placed, or one that is not to linger', () => {
    const { cursor, pointer, onActive } = setup()
    cursor.handle(working(60_000))
    expect([cursor.active, cursor.el.hidden, pointer.hidden]).toEqual([true, true, true])
    expect(onActive).toHaveBeenCalledWith(true, 'Claude Code · notes', '', '')
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
