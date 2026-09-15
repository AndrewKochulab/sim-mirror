// SPDX-License-Identifier: Apache-2.0
/**
 * What the viewer says about the device: its name, the state pill, what an agent is doing or the device is busy with,
 * whether the mirror can only show the screen, and -- when there is no screen to show -- why, with a way on.
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
  state: HTMLElement
  mode: HTMLElement
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
  let agent: string | null = null
  let bootTimer: number | null = null

  function paintBadge(): void {
    const text = agent ? `${agent} is using this device` : device?.busy ? `Busy: ${device.busy}` : ''
    parts.badge.textContent = text
    parts.badge.hidden = !text
  }

  function say(message: string | null, offer?: Offer): void {
    parts.empty.hidden = message === null
    parts.canvas.hidden = message !== null
    parts.empty.innerHTML = message === null ? ''
      : `<p>${escapeHTML(message)}</p>` + (offer
        ? `<button type="button" class="smv-btn" data-smv="start">${offer === 'start' ? 'Start the simulator' : 'Try again'}</button>`
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
    showState(next?.state ?? 'stopped')
    paintBadge()
    if (!next) return
    if (next.state === 'ready') {
      say(null)
    } else if (next.state === 'booting') {
      const began = Date.now() - next.since_ms
      const tick = () => say(`Starting ${next.name}… ${Math.round((Date.now() - began) / 1000)}s`)
      tick()
      bootTimer = window.setInterval(tick, BOOT_TICK_MS)
    } else if (next.state === 'stalled') {
      say(`${next.reason || 'The simulator stopped answering'}. Reconnecting…`)
    } else if (next.state === 'failed') {
      say(next.reason || 'The simulator could not start.', 'retry')
    } else {
      say('The simulator is stopped.', 'start')
    }
  }

  function setMode(hello: ServerHello | null): void {
    const viewOnly = hello !== null && !hello.capabilities.includes('input_touch')
    parts.mode.hidden = !viewOnly
    parts.mode.title = hello && viewOnly
      ? hello.fallback_reason ?? `The ${hello.connector} connector shows the screen but cannot touch it.`
      : ''
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
