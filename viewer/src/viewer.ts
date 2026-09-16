// SPDX-License-Identifier: Apache-2.0
/**
 * A device's iOS Simulator: its live screen, a person's touches and keys, and what an agent is doing on it.
 *
 * `createViewer` draws into any element and talks to its server through a transport (`createHttpTransport`, or a
 * host's own). The screen is a real device's, frame by frame over a ticketed socket (`viewer-stream.ts`), drawn keeping
 * the device's shape (`screen-canvas.ts`). Nothing a person does reaches the device but the protocol's short list of
 * input (`viewer-input.ts`). What an agent does arrives on the same socket before it happens, and is drawn over the
 * screen (`agent-cursor.ts`): a pointer of the viewer's own, never the machine's.
 *
 * The same view can dock beside a page, fill a window, or have a page of its own; a host moves its element between
 * those without the socket noticing, so a device is never reconnected to be moved.
 */
import { createAgentCursor } from './agent-cursor'
import { canDecodeH264 } from './h264-stream'
import { lucideSvg, type IconRenderer } from './icons'
import { createLayers } from './popover'
import type { Capability, Device, Encoding, ServerHello } from './protocol.generated'
import { createScreenCanvas, fitRect } from './screen-canvas'
import { createStatusView } from './status-view'
import { adoptStyles } from './styles'
import type { SimMirrorTransport } from './transport'
import { barMarkup, createControls, type Placement } from './viewer-controls'
import { attachInput } from './viewer-input'
import { createViewerStream } from './viewer-stream'

export type { Placement } from './viewer-controls'

export interface ViewerState {
  device: Device | null
  connector: string | null
  capabilities: readonly Capability[]
  /** The connector can show the screen but not touch it. */
  viewOnly: boolean
  connected: boolean
}

export interface ViewerOptions {
  transport: SimMirrorTransport
  /** Where the view is shown; `page` when not given. */
  placement?: Placement
  /** Move the view between a dock and a window; not offered when absent, nor on a page. */
  onPlace?(next: 'dock' | 'window'): void
  /** Where the view has a page of its own; not offered when absent, nor on that page. */
  pageHref?: string
  onClose?(): void
  /** Whether the agent's pointer jumps rather than glides; the system setting when absent. */
  reducedMotion?(): boolean
  /** Whether this page can decode H.264 itself; WebCodecs in a secure page when absent. */
  canDecodeH264?(): boolean
  /** Draws the viewer's icons; Lucide's, inline, when absent. */
  icon?: IconRenderer
  /** Told whenever the device, what the server offers, or the connection changes. */
  onState?(state: ViewerState): void
}

export interface ViewHandle {
  readonly el: HTMLElement
  readonly device: Device | null
  readonly hello: ServerHello | null
  /** How frames come on this connection, once the server has said. */
  readonly encoding: Encoding | null
  connected(): boolean
  /** Connect while active; let the device's screen go while not. */
  setActive(on: boolean): void
  setPlacement(placement: Placement): void
  focus(): void
  destroy(): void
}

const STAGE = `
    <div class="smv-stage" data-smv-stage part="stage">
      <canvas class="smv-canvas" data-smv-canvas tabindex="0" aria-label="iOS Simulator screen" part="screen"></canvas>
      <div class="smv-overlay" data-smv-overlay></div>
      <div class="smv-badge" data-smv-badge hidden></div>
      <div class="smv-empty" data-smv-empty hidden></div>
      <div class="smv-picker" data-smv-picker role="menu" hidden></div>
      <div class="smv-settings" data-smv-settings role="dialog" hidden></div>
    </div>`

export function createViewer(host: HTMLElement | ShadowRoot, options: ViewerOptions): ViewHandle {
  const icon = options.icon ?? lucideSvg
  const el = document.createElement('div')
  el.className = 'smv'
  el.innerHTML = barMarkup(icon) + STAGE
  host.appendChild(el)
  adoptStyles(el)

  const q = <T extends Element>(selector: string) => el.querySelector<T>(selector)!
  const canvas = q<HTMLCanvasElement>('[data-smv-canvas]')
  const overlay = q<HTMLElement>('[data-smv-overlay]')
  let active = false
  let destroyed = false
  let placement: Placement = options.placement ?? 'page'

  const screen = createScreenCanvas(canvas, { fit: 'contain' })
  const status = createStatusView({
    canvas, empty: q('[data-smv-empty]'), badge: q('[data-smv-badge]'), name: q('[data-smv-name]'),
    state: q('[data-smv-state]'), mode: q('[data-smv-mode]'),
  })
  /** The screen's shape: the device's points once it says, the canvas's until then. */
  const unitsOf = () => stream.device?.screen?.points ?? { w: canvas.width, h: canvas.height }
  const cursor = createAgentCursor(overlay, {
    frameBox: () => {
      const box = overlay.getBoundingClientRect()
      return fitRect({ w: box.width, h: box.height }, unitsOf())
    },
    reducedMotion: options.reducedMotion,
    icon,
    onActive: (on, title) => status.setAgent(on ? title : null),
  })
  const stream = createViewerStream({
    transport: options.transport, screen, cursor, status, unitsOf,
    canDecodeH264: options.canDecodeH264 ?? (() => canDecodeH264()),
    wanted: () => active && !destroyed,
    onHello: (hello) => {
      status.setMode(hello)
      controls.applyHello(hello)
    },
    onChange: () => {
      const hello = stream.hello
      options.onState?.({
        device: stream.device, connector: hello?.connector ?? null, capabilities: hello?.capabilities ?? [],
        viewOnly: hello !== null && !hello.capabilities.includes('input_touch'), connected: stream.open,
      })
    },
    onEnd: () => input.releaseFinger(),
  })
  /** Menus open over the screen; while one is, the screen's input holds back. */
  const layers = createLayers()
  const input = attachInput({
    canvas, screen, send: (message) => stream.send(message), allows: stream.allows, blocked: () => layers.open,
  })
  const controls = createControls({
    el, picker: q('[data-smv-picker]'), settingsPanel: q('[data-smv-settings]'), transport: options.transport, stream,
    status, input, icon, layers,
    placement: () => placement, onPlace: options.onPlace, pageHref: options.pageHref, onClose: options.onClose,
  })
  controls.paintPlacement()

  return {
    el,
    get device() {
      return stream.device
    },
    get hello() {
      return stream.hello
    },
    get encoding() {
      return stream.encoding
    },
    connected: () => stream.open,
    setActive(on) {
      if (on === active) return
      active = on
      if (on) {
        void stream.connect()
      } else {
        input.flushTyped()
        stream.close()
        status.stopBootTimer()
        stream.cancelReconnect()
      }
    },
    setPlacement(next) {
      placement = next
      controls.paintPlacement()
    },
    focus: () => canvas.focus(),
    destroy() {
      destroyed = true
      status.stopBootTimer()
      stream.cancelReconnect()
      input.destroy()
      controls.destroy()
      cursor.destroy()
      screen.destroy()
      stream.close()
      el.remove()
    },
  }
}
