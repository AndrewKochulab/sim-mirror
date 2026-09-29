// SPDX-License-Identifier: Apache-2.0
/**
 * What the viewer says about the device: its name, whether it is a real device and how it is connected, the state
 * pill, what an agent is doing or the device is busy with, whether the mirror can only show the screen, which app in
 * front shares its view hierarchy through SimMirror's debug SDK, and -- when there is no screen to show -- why, with a
 * way on.
 */
import { escapeHTML } from './escape'
import { DEVICE_STATES, type Device, type DeviceState, type ServerHello } from './protocol.generated'

export const STATE_LABELS: Readonly<Record<DeviceState, string>> = {
  booting: 'Starting', ready: 'Ready', stalled: 'Reconnecting', failed: 'Failed', stopped: 'Stopped',
}
const BOOT_TICK_MS = 1000

export type Offer = 'start' | 'retry'

export interface StatusParts {
  canvas: HTMLCanvasElement
  empty: HTMLElement
  badge: HTMLElement
  name: HTMLElement
  /** The chip that says a device is a real one, and how it is connected. */
  kind: HTMLElement
  state: HTMLElement
  mode: HTMLElement
  app: HTMLElement
}

export interface StatusView {
  /** Show why there is no screen -- with a way on, when there is one -- or, given null, the screen. */
  say(message: string | null, offer?: Offer): void
  showState(state: DeviceState): void
  applyDevice(device: Device | null): void
  /** An agent is using the device, by this title; null when none is. */
  setAgent(title: string | null): void
  /** What the server said it offers: a mirror whose connector cannot touch the screen says so. */
  setMode(hello: ServerHello | null): void
  stopBootTimer(): void
}

/** A device's state from the screen socket, checked -- or null for anything that is not one. */
export function readDevice(message: Record<string, unknown>): Device | null {
  const valid = typeof message.udid === 'string' && typeof message.name === 'string'
    && (DEVICE_STATES as readonly unknown[]).includes(message.state)
  return valid ? (message as unknown as Device) : null
}

export function createStatusView(parts: StatusParts): StatusView {
  let device: Device | null = null
  let hello: ServerHello | null = null
  let agent: string | null = null
  let bootTimer: number | null = null

  function paintBadge(): void {
    const text = agent ? `${agent} is using this device` : device?.busy ? `Busy: ${device.busy}` : ''
    parts.badge.textContent = text
    parts.badge.hidden = !text
  }

  /** The app sharing its view hierarchy, by the name the app gives: always set as text, never as markup. */
  function paintApp(): void {
    const shared = device?.app_hierarchy ?? null
    const said = shared
      ? `${shared.name} (${shared.bundle_id}) shares its view hierarchy through SimMirror's SDK ${shared.sdk_version}`
      : ''
    parts.app.hidden = shared === null
    parts.app.textContent = shared ? `${shared.name} · SDK` : ''
    parts.app.title = said
    if (shared) parts.app.setAttribute('aria-label', said)
    else parts.app.removeAttribute('aria-label')
  }

  /** What starting says: a real device is not started, only shown again. */
  const startLabel = () => (device?.kind === 'physical' ? 'Show the device' : 'Start the simulator')

  function say(message: string | null, offer?: Offer): void {
    parts.empty.hidden = message === null
    parts.canvas.hidden = message !== null
    parts.empty.innerHTML = message === null ? ''
      : `<p>${escapeHTML(message)}</p>` + (offer
        ? `<button type="button" class="smv-btn" data-smv="start">${offer === 'start' ? startLabel() : 'Try again'}</button>`
        : '')
  }

  function stopBootTimer(): void {
    if (bootTimer !== null) window.clearInterval(bootTimer)
    bootTimer = null
  }

  function showState(state: DeviceState): void {
    parts.state.dataset.state = state
    parts.state.textContent = STATE_LABELS[state]
  }

  function applyDevice(next: Device | null): void {
    device = next
    stopBootTimer()
    parts.name.textContent = next ? `${next.name} · ${next.runtime}` : 'iOS Simulator'
    const physical = next?.kind === 'physical'
    paintMode()
    parts.kind.hidden = !physical
    parts.kind.textContent = physical ? `Real device · ${next.connection === 'usb' ? 'USB' : 'Wi-Fi'}` : ''
    const noun = physical ? 'device' : 'simulator'
    showState(next?.state ?? 'stopped')
    paintBadge()
    paintApp()
    if (!next) return
    if (next.state === 'ready') {
      say(null)
    } else if (next.state === 'booting') {
      const began = Date.now() - next.since_ms
      const tick = () => say(`Starting ${next.name}… ${Math.round((Date.now() - began) / 1000)}s`)
      tick()
      bootTimer = window.setInterval(tick, BOOT_TICK_MS)
    } else if (next.state === 'stalled') {
      say(`${next.reason || `The ${noun} stopped answering`}. Reconnecting…`)
    } else if (next.state === 'failed') {
      say(next.reason || `The ${noun} could not start.`, 'retry')
    } else {
      say(`The ${noun} is stopped.`, 'start')
    }
  }

  /** A mirror that cannot touch says so; a real device's offers what would let it be touched. */
  function paintMode(): void {
    const viewOnly = hello !== null && !hello.capabilities.includes('input_touch')
    parts.mode.hidden = !viewOnly
    parts.mode.textContent = viewOnly && device?.kind === 'physical' ? 'Set up touch' : 'View only'
    parts.mode.title = hello && viewOnly
      ? hello.fallback_reason ?? `The ${hello.connector} connector shows the screen but cannot touch it.`
      : ''
  }

  function setMode(next: ServerHello | null): void {
    hello = next
    paintMode()
  }

  return {
    say,
    showState,
    applyDevice,
    setAgent(title) {
      agent = title
      paintBadge()
    },
    setMode,
    stopBootTimer,
  }
}
