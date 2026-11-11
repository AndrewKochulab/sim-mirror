// SPDX-License-Identifier: Apache-2.0
/**
 * Where an agent is acting on a device's screen, drawn over it by the viewer.
 *
 * Never the Mac's own pointer: a person keeps using the machine while an agent works, so what an agent does is shown
 * here, over the screen, and nowhere else. The server announces each gesture before it plays it (an ``agent`` event
 * with phase ``intent``), waiting `lead_ms` while someone watches, so the pointer glides to where a tap will land and
 * the tap lands under it. Then:
 *
 * * a **tap** ripples where it lands, a **long press** fills a ring there for as long as it holds;
 * * a **swipe** or **drag** leaves its path as a trail, and the pointer follows it.
 *
 * **What the agent is doing is said, briefly**: the server's ``activity`` -- Tapping “Add”, Typing…, Reading the screen,
 * Building -- is the pointer's label, and `onActive` hands it on with the agent's name for the viewer's pill. A server
 * that says none is shown its name.
 *
 * ``done`` says whether it worked; a failure shakes the pointer. With reduced motion the pointer jumps instead of gliding,
 * and a ring fades where it is without growing.
 *
 * **An idle pointer gets out of the way.** `PARK_AFTER_MS` after the agent's last gesture the pointer glides to the
 * screen's bottom-right corner, dimmed and smaller, labelled "Idle" -- or with what the agent is doing meanwhile, a build
 * say -- and the next gesture brings it back from there.
 *
 * **The pointer stays while the agent works.** Once a gesture is done it rests, dimmed, where it landed, for the
 * event's `linger_ms` (``agent.cursor_linger_s``) after the agent's last event. A tool call says the agent is
 * ``working`` when it starts and every `WORKING_EVERY_S` while it runs -- a build, a test run -- and that it has ended
 * when it ends, so the pointer stays through the agent's whole run, however short its linger, and leaves once it goes
 * quiet. An agent counts as using the device for as long, and never for less than `activeMs`. The pointer is drawn here, over the screen, never into it: a screenshot
 * or a recording of the device does not show it.
 */
import { WORKING_EVERY_S, type Agent, type AgentDone, type AgentEvent, type AgentIntent, type AgentWorking,
  type Point } from './protocol.generated'
import type { Rect } from './screen-canvas'

export interface AgentCursorOptions {
  /** Where the device's screen is drawn inside the overlay, in CSS pixels. */
  frameBox(): Rect
  /** Whether to jump rather than glide; the person's system setting when not given. */
  reducedMotion?(): boolean
  /** The least time an agent counts as using the device, and the pointer stays, after its last event. */
  activeMs?: number
  /** Told when an agent starts or stops using the device, what it is called, what it is doing now -- '' when it says
   * nothing more -- and the colour it is drawn in ('' for the viewer's own), again whenever any of that changes, so
   * whatever names the agent can match its pointer. */
  onActive?(active: boolean, title: string | null, activity: string, color: string): void
}

export interface AgentCursorHandle {
  readonly el: HTMLElement
  readonly active: boolean
  handle(event: AgentEvent): void
  destroy(): void
}

export const ACTIVE_MS = 5000
export const RIPPLE_MS = 600
export const FAILED_MS = 900
/** How quickly the pointer dims to rest, or wakes. */
export const REST_MS = 300
/** How long a call that is still running holds the pointer: past its next beat, with room for one to be late. */
export const ONGOING_MS = WORKING_EVERY_S * 1500
/** The longest glide: a lead longer than this is spent waiting under the target, not travelling to it. */
export const GLIDE_MAX_MS = 400
/** How long after its last gesture lands an idle pointer goes to the corner, and how long it takes to get there. */
export const PARK_AFTER_MS = 5000
export const PARK_GLIDE_MS = 600
/** How far in from the screen's bottom-right corner a parked pointer waits, in CSS pixels. */
export const PARK_INSET = 32
/** A pointer this close to the screen's right edge has its label on its left, where it fits. */
export const FLIP_WITHIN = 150
export const IDLE_LABEL = 'Idle'
/** How long the pointer presses down as a tap lands. */
export const PRESS_MS = 160
/** The pointer's shape: a rounded arrow whose tip is at (4, 4) of a 24-pixel box, drawn by SimMirror rather than taken
 * from an icon set, so every host shows the same pointer. */
export const POINTER_PATH = 'M5.2 4.1 20.1 10.2Q21.3 10.8 20.1 11.3L13.6 13.6 11.3 20.1Q10.8 21.3 10.2 20.1L4.1 5.2Q3.7 3.7 5.2 4.1Z'
let pointers = 0

/** The pointer, its fill a gradient of the accent colour: its own gradient id, so two viewers on a page never share
 * one. */
export function pointerSvg(): string {
  pointers += 1
  const id = `smv-ac-fill-${pointers}`
  return '<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" data-ac-shape>'
    + `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0.4" y2="1">`
    + '<stop offset="0" class="ac-fill-top"/><stop offset="1" class="ac-fill"/></linearGradient></defs>'
    + `<path d="${POINTER_PATH}" fill="url(#${id})"/></svg>`
}
const TRACED = new Set(['swipe', 'drag'])
const SVG = 'http://www.w3.org/2000/svg'

const isText = (value: unknown): value is string => typeof value === 'string'
const COLOR = /^#[0-9a-f]{6}$/i
const colorOf = (value: unknown): string => (isText(value) && COLOR.test(value) ? value.toLowerCase() : '')

/** The minimum contrast white text keeps on the pointer's label -- WCAG's for bold text -- before near-black is used
 * instead. White on a system blue or a violet reads as people expect; on a yellow or a light green, it does not. */
export const WHITE_TEXT_CONTRAST = 3
/** The text colour for a pointer of colour `hex` (``#rrggbb``): white, unless it would have less than
 * `WHITE_TEXT_CONTRAST` against it as WCAG measures it, and then near-black. */
export function textOn(hex: string): string {
  const channel = (at: number) => {
    const value = parseInt(hex.slice(at, at + 2), 16) / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  }
  const light = 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)
  return 1.05 / (light + 0.05) >= WHITE_TEXT_CONTRAST ? '#ffffff' : '#111113'
}
const isWhole = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0
const isShare = (value: unknown): boolean => typeof value === 'number' && value >= 0 && value <= 1

function readAgent(raw: unknown): Agent | null {
  const agent = raw as Record<string, unknown> | null
  return agent && typeof agent === 'object' && isText(agent.key) && isText(agent.title)
    ? { key: agent.key, title: agent.title }
    : null
}

/** An agent event from the screen socket, checked -- or null for anything that is not one. */
export function readAgentEvent(raw: unknown): AgentEvent | null {
  if (!raw || typeof raw !== 'object') return null
  const event = raw as Record<string, unknown>
  if (event.type !== 'agent' || !isText(event.id)) return null
  if (event.phase === 'done') {
    return typeof event.ok === 'boolean' ? { type: 'agent', id: event.id, phase: 'done', ok: event.ok } : null
  }
  const agent = readAgent(event.agent)
  // A server from before it lingered sends none: the pointer goes once the gesture is drawn, as it did then.
  const linger = isWhole(event.linger_ms) ? event.linger_ms : 0
  if (event.phase === 'working') {
    return agent
      ? { type: 'agent', id: event.id, phase: 'working', agent, ongoing: event.ongoing === true, linger_ms: linger,
          activity: isText(event.activity) ? event.activity : '', color: colorOf(event.color) }
      : null
  }
  const gesture = event.gesture as Record<string, unknown> | null
  if (event.phase !== 'intent' || !agent || !gesture || typeof gesture !== 'object' || !isText(gesture.kind)
      || !isWhole(gesture.duration_ms) || !Array.isArray(gesture.points) || !isWhole(event.lead_ms)) return null
  const points = gesture.points as unknown[]
  if (!points.every((p) => Array.isArray(p) && p.length === 2 && isShare(p[0]) && isShare(p[1]))) return null
  return {
    type: 'agent', id: event.id, phase: 'intent', agent,
    gesture: { kind: gesture.kind, duration_ms: gesture.duration_ms, points: points as Point[] },
    label: isText(event.label) ? event.label : '', caption: isText(event.caption) ? event.caption : '',
    activity: isText(event.activity) ? event.activity : '', color: colorOf(event.color), lead_ms: event.lead_ms, linger_ms: linger, pointer: event.pointer !== false,
  }
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function createAgentCursor(overlay: HTMLElement, options: AgentCursorOptions): AgentCursorHandle {
  const el = document.createElement('div')
  el.className = 'ac'
  el.hidden = true
  el.setAttribute('aria-hidden', 'true')
  // The pointer shows once a gesture places it: an agent that only looks, launches or builds has no point on the
  // screen, and the viewer's pill says what it does.
  el.innerHTML = `<div class="ac-pointer" data-ac-pointer hidden><span class="ac-arrow">${pointerSvg()}</span>`
    + '<span class="ac-chip" data-ac-chip></span></div>'
  overlay.appendChild(el)
  const pointer = el.querySelector<HTMLElement>('[data-ac-pointer]')!
  const chip = el.querySelector<HTMLElement>('[data-ac-chip]')!
  const timers = new Set<number>()
  const activeMs = options.activeMs ?? ACTIVE_MS
  const reduced = () => (options.reducedMotion ? options.reducedMotion() : prefersReducedMotion())
  let idle: number | null = null
  let rest: number | null = null
  let park: number | null = null
  let active = false
  let lingerMs = 0
  /** Until when a call still running holds everything, whatever lingers: its next beat is due before then. */
  let heldUntil = 0
  /** Where the pointer last landed, to rest it there again; null until a gesture places it. */
  let last: Point | null = null
  let parked = false
  let title = ''
  /** What the agent is doing now, as the server said it; '' when it says nothing more than that it works. */
  let doing = ''
  /** The scope's colour for the agent, as the server last said it; '' for the viewer's own. */
  let hue = ''
  /** What was last told, so `onActive` hears each change once. */
  let told = ''
  const stayMs = () => Math.max(activeMs, lingerMs, heldUntil - Date.now())

  function later(ms: number, work: () => void): void {
    const id = window.setTimeout(() => {
      timers.delete(id)
      work()
    }, ms)
    timers.add(id)
  }

  function at(point: Point): { x: number; y: number } {
    const box = options.frameBox()
    return { x: box.x + point[0] * box.w, y: box.y + point[1] * box.h }
  }

  /** The label beside the pointer: what the agent does, else that it is idle, else who it is. */
  function paintChip(): void {
    chip.textContent = doing || (parked ? IDLE_LABEL : title)
  }

  function tell(): void {
    const now = active ? `${title}\n${doing}\n${hue}` : ''
    if (now === told) return
    told = now
    options.onActive?.(active, active ? title : null, active ? doing : '', active ? hue : '')
  }

  function say(activity: string, ended = false): void {
    if (activity) doing = activity
    else if (ended) doing = ''
    paintChip()
  }

  function moveTo(spot: { x: number; y: number }, travelMs: number): void {
    // Two durations, for the two properties it moves: how far it travels, and how it dims or wakes.
    pointer.style.transitionDuration = reduced() ? '0ms, 0ms' : `${Math.round(travelMs)}ms, ${REST_MS}ms`
    pointer.style.transform = `translate(${spot.x}px, ${spot.y}px)`
    const box = options.frameBox()
    pointer.classList.toggle('is-flipped', parked || spot.x > box.x + box.w - FLIP_WITHIN)
    pointer.hidden = false
  }

  function place(point: Point, travelMs: number, resting = false): { x: number; y: number } {
    const spot = at(point)
    last = point
    parked = false
    pointer.classList.remove('is-parked')
    moveTo(spot, travelMs)
    pointer.classList.toggle('is-resting', resting)
    paintChip()
    return spot
  }

  /** Out of the way: to the screen's bottom-right corner, dimmed and smaller, until the next gesture. */
  function toCorner(travelMs: number): void {
    const box = options.frameBox()
    parked = true
    pointer.classList.add('is-parked')
    pointer.classList.remove('is-resting')
    moveTo({ x: box.x + box.w - PARK_INSET, y: box.y + box.h - PARK_INSET }, travelMs)
    paintChip()
  }

  function parkLater(ms: number): void {
    if (park !== null) window.clearTimeout(park)
    park = window.setTimeout(() => {
      park = null
      if (!pointer.hidden) toCorner(PARK_GLIDE_MS)
    }, ms)
  }

  /** The pointer presses down as a tap lands under it, and lets go -- not with reduced motion. */
  function press(): void {
    if (reduced()) return
    pointer.classList.add('is-pressing')
    later(PRESS_MS, () => pointer.classList.remove('is-pressing'))
  }

  /** Something drawn for a while at a spot on the screen. Placed by its own left and top, so the scale its animation
   * grows by is about the spot: a transform here would be scaled with it, and the ring would sweep in from the
   * overlay's corner and on past the spot. */
  function mark(className: string, spot: { x: number; y: number }, ms: number): HTMLElement {
    const node = document.createElement('div')
    node.className = className
    node.classList.toggle('is-still', reduced())
    node.style.left = `${spot.x}px`
    node.style.top = `${spot.y}px`
    el.appendChild(node)
    later(ms, () => node.remove())
    return node
  }

  function trail(points: Point[], ms: number): void {
    const svg = document.createElementNS(SVG, 'svg')
    svg.setAttribute('class', 'ac-trail')
    const line = document.createElementNS(SVG, 'polyline')
    line.setAttribute('points', points.map((p) => at(p)).map((s) => `${s.x},${s.y}`).join(' '))
    svg.appendChild(line)
    el.appendChild(svg)
    later(ms, () => svg.remove())
  }

  /** An agent is using the device, for as long as it lingers from now: whatever is drawn, someone is told. */
  function markActive(named: string | null): void {
    if (named !== null) title = named
    active = true
    paintChip()
    tell()
    if (idle !== null) window.clearTimeout(idle)
    idle = window.setTimeout(() => {
      idle = null
      active = false
      doing = ''
      tell()
    }, stayMs())
  }

  /** Keep what is drawn for as long as it lingers from now, then let it go. */
  function keepDrawn(): void {
    if (rest !== null) window.clearTimeout(rest)
    rest = window.setTimeout(() => {
      rest = null
      el.hidden = true
      pointer.hidden = true
      parked = false
      pointer.classList.remove('is-parked')
      if (park !== null) window.clearTimeout(park)
      park = null
    }, stayMs())
  }

  /** The scope's own colour for the agent, with text that reads on it; none, and the viewer's accent is used. */
  function paint(color: string): void {
    hue = color
    if (color) {
      el.style.setProperty('--ac-color', color)
      el.style.setProperty('--ac-text', textOn(color))
    } else {
      el.style.removeProperty('--ac-color')
      el.style.removeProperty('--ac-text')
    }
  }

  function intent(event: AgentIntent): void {
    lingerMs = event.linger_ms
    paint(event.color)
    // A server from before activities says what it lands on, or what it types: that, rather than nothing.
    say(event.activity || event.caption || event.label)
    markActive(event.agent.title)
    keepDrawn()
    if (!event.pointer) return
    const { kind, duration_ms: duration, points } = event.gesture
    // Nothing on the screen -- launching an app, a build: the pill says it, and a pointer already shown says it too.
    if (points.length === 0) return
    el.hidden = false
    const lead = event.lead_ms
    const start = place(points[0], Math.min(lead, GLIDE_MAX_MS))
    parkLater(lead + duration + PARK_AFTER_MS)
    if (kind === 'tap' || kind === 'right_click') {
      later(lead, () => {
        mark('ac-ripple', start, RIPPLE_MS)
        press()
      })
    } else if (kind === 'long_press') {
      later(lead, () => {
        mark('ac-hold', start, duration + RIPPLE_MS).style.animationDuration = `${duration}ms`
      })
    } else if (TRACED.has(kind) && points.length > 1) {
      trail(points, lead + duration + RIPPLE_MS)
      later(lead, () => place(points[points.length - 1], duration))
    }
  }

  function done(event: AgentDone): void {
    markActive(null)
    keepDrawn()
    if (!pointer.hidden && !parked) pointer.classList.add('is-resting')
    if (event.ok) return
    pointer.classList.add('is-failed')
    later(FAILED_MS, () => pointer.classList.remove('is-failed'))
  }

  /** Still at work, with nothing new to draw: the pointer rests where it last landed -- or waits in the corner -- for
   * as long as it lingers, saying what the agent does now. */
  function working(event: AgentWorking): void {
    lingerMs = event.linger_ms
    paint(event.color)
    heldUntil = event.ongoing ? Date.now() + ONGOING_MS : 0
    say(event.activity, !event.ongoing)
    markActive(event.agent.title)
    if (lingerMs === 0 || last === null) return
    keepDrawn()
    el.hidden = false
    if (parked) toCorner(0)
    else place(last, 0, true)
  }

  return {
    el,
    get active() {
      return active
    },
    handle(event) {
      if (event.phase === 'intent') intent(event)
      else if (event.phase === 'working') working(event)
      else done(event)
    },
    destroy() {
      timers.forEach((id) => window.clearTimeout(id))
      timers.clear()
      for (const id of [idle, rest, park]) if (id !== null) window.clearTimeout(id)
      idle = rest = park = null
      el.remove()
    },
  }
}
