// SPDX-License-Identifier: Apache-2.0
/**
 * Where an agent is acting on a device's screen, drawn over it by the viewer.
 *
 * Never the Mac's own pointer: a person keeps using the machine while an agent works, so what an agent does is shown
 * here, over the screen, and nowhere else. The server announces each gesture before it plays it (an ``agent`` event
 * with phase ``intent``), waiting `lead_ms` while someone watches, so the pointer glides to where a tap will land and
 * the tap lands under it. Then:
 *
 * * a **tap** ripples, a **long press** fills a ring for as long as it holds;
 * * a **swipe** or **drag** leaves its path as a trail, and the pointer follows it;
 * * anything else -- typing, a button, launching an app, a build -- is a caption.
 *
 * ``done`` says whether it worked; a failure shakes the pointer. An agent counts as using the device for `activeMs`
 * after its last event. With reduced motion the pointer jumps instead of gliding.
 */
import { lucideSvg, type IconRenderer } from './icons'
import type { AgentDone, AgentEvent, AgentIntent, Point } from './protocol.generated'
import type { Rect } from './screen-canvas'

export interface AgentCursorOptions {
  /** Where the device's screen is drawn inside the overlay, in CSS pixels. */
  frameBox(): Rect
  /** Whether to jump rather than glide; the person's system setting when not given. */
  reducedMotion?(): boolean
  /** How long an agent counts as using the device after its last event. */
  activeMs?: number
  /** Told when an agent starts or stops using the device, and what it is called. */
  onActive?(active: boolean, title: string | null): void
  icon?: IconRenderer
}

export interface AgentCursorHandle {
  readonly el: HTMLElement
  readonly active: boolean
  handle(event: AgentEvent): void
  destroy(): void
}

export const ACTIVE_MS = 5000
export const RIPPLE_MS = 600
export const CAPTION_MIN_MS = 1400
export const FAILED_MS = 900
/** The longest glide: a lead longer than this is spent waiting under the target, not travelling to it. */
export const GLIDE_MAX_MS = 400
const TRACED = new Set(['swipe', 'drag'])
const SVG = 'http://www.w3.org/2000/svg'

const isText = (value: unknown): value is string => typeof value === 'string'
const isWhole = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0
const isShare = (value: unknown): boolean => typeof value === 'number' && value >= 0 && value <= 1

/** An agent event from the screen socket, checked -- or null for anything that is not one. */
export function readAgentEvent(raw: unknown): AgentEvent | null {
  if (!raw || typeof raw !== 'object') return null
  const event = raw as Record<string, unknown>
  if (event.type !== 'agent' || !isText(event.id)) return null
  if (event.phase === 'done') {
    return typeof event.ok === 'boolean' ? { type: 'agent', id: event.id, phase: 'done', ok: event.ok } : null
  }
  const agent = event.agent as Record<string, unknown> | null
  const gesture = event.gesture as Record<string, unknown> | null
  if (event.phase !== 'intent' || !agent || typeof agent !== 'object' || !isText(agent.key) || !isText(agent.title)
      || !gesture || typeof gesture !== 'object' || !isText(gesture.kind) || !isWhole(gesture.duration_ms)
      || !Array.isArray(gesture.points) || !isWhole(event.lead_ms)) return null
  const points = gesture.points as unknown[]
  if (!points.every((p) => Array.isArray(p) && p.length === 2 && isShare(p[0]) && isShare(p[1]))) return null
  return {
    type: 'agent', id: event.id, phase: 'intent', agent: { key: agent.key, title: agent.title },
    gesture: { kind: gesture.kind, duration_ms: gesture.duration_ms, points: points as Point[] },
    label: isText(event.label) ? event.label : '', caption: isText(event.caption) ? event.caption : '',
    lead_ms: event.lead_ms, pointer: event.pointer !== false,
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
  // screen, and a pointer parked in the overlay's corner would claim one.
  const arrow = (options.icon ?? lucideSvg)('pointer')
  el.innerHTML = `<div class="ac-pointer" data-ac-pointer hidden><span class="ac-arrow">${arrow}</span>`
    + '<span class="ac-chip" data-ac-chip></span></div>'
  overlay.appendChild(el)
  const pointer = el.querySelector<HTMLElement>('[data-ac-pointer]')!
  const chip = el.querySelector<HTMLElement>('[data-ac-chip]')!
  const timers = new Set<number>()
  const activeMs = options.activeMs ?? ACTIVE_MS
  const reduced = () => (options.reducedMotion ? options.reducedMotion() : prefersReducedMotion())
  let idle: number | null = null
  let active = false

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

  function place(point: Point, travelMs: number): { x: number; y: number } {
    const spot = at(point)
    pointer.style.transitionDuration = `${reduced() ? 0 : Math.round(travelMs)}ms`
    pointer.style.transform = `translate(${spot.x}px, ${spot.y}px)`
    pointer.hidden = false
    return spot
  }

  /** Something drawn for a while at a spot on the screen -- or, with none, along its top. */
  function mark(className: string, spot: { x: number; y: number } | null, ms: number, text = ''): HTMLElement {
    const node = document.createElement('div')
    node.className = className
    if (spot) node.style.transform = `translate(${spot.x}px, ${spot.y}px)`
    node.textContent = text
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

  /** An agent is using the device, for `activeMs` from now: whatever is drawn, someone is told. */
  function markActive(title: string | null): void {
    if (title !== null) chip.textContent = title
    if (!active) {
      active = true
      options.onActive?.(true, chip.textContent)
    }
    if (idle !== null) window.clearTimeout(idle)
    idle = window.setTimeout(() => {
      idle = null
      active = false
      el.hidden = true
      pointer.hidden = true
      options.onActive?.(false, null)
    }, activeMs)
  }

  function intent(event: AgentIntent): void {
    markActive(event.agent.title)
    if (!event.pointer) return
    el.hidden = false
    const { kind, duration_ms: duration, points } = event.gesture
    const lead = event.lead_ms
    const text = event.caption || event.label
    if (points.length === 0) {
      if (text) mark('ac-caption ac-caption-top', null, Math.max(CAPTION_MIN_MS, duration), text)
      return
    }
    const start = place(points[0], Math.min(lead, GLIDE_MAX_MS))
    if (kind === 'tap') {
      later(lead, () => mark('ac-ripple', start, RIPPLE_MS))
    } else if (kind === 'long_press') {
      later(lead, () => {
        mark('ac-hold', start, duration + RIPPLE_MS).style.animationDuration = `${duration}ms`
      })
    } else if (TRACED.has(kind) && points.length > 1) {
      trail(points, lead + duration + RIPPLE_MS)
      later(lead, () => place(points[points.length - 1], duration))
    } else if (text) {
      mark('ac-caption', start, Math.max(CAPTION_MIN_MS, lead + duration), text)
    }
  }

  function done(event: AgentDone): void {
    markActive(null)
    if (event.ok) return
    pointer.classList.add('is-failed')
    later(FAILED_MS, () => pointer.classList.remove('is-failed'))
  }

  return {
    el,
    get active() {
      return active
    },
    handle(event) {
      if (event.phase === 'intent') intent(event)
      else done(event)
    },
    destroy() {
      timers.forEach((id) => window.clearTimeout(id))
      timers.clear()
      if (idle !== null) window.clearTimeout(idle)
      idle = null
      el.remove()
    },
  }
}
