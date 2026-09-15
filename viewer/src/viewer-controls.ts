// SPDX-License-Identifier: Apache-2.0
/**
 * The viewer's toolbar: Home and Lock, the appearance, the device picker, where the view is placed, letting the device
 * go, and closing. A button the device's connector cannot press is not offered once the server has said what it can do.
 */
import { escapeHTML } from './escape'
import type { IconName, IconRenderer } from './icons'
import type { Capability, DeviceChoice, ServerHello } from './protocol.generated'
import { STATE_LABELS, type StatusView } from './status-view'
import type { SimMirrorTransport } from './transport'
import type { ViewerInput } from './viewer-input'
import type { ViewerStream } from './viewer-stream'

export type Placement = 'dock' | 'window' | 'page'

/** The buttons that need the connector to be able to do something. */
const NEEDS: ReadonlyArray<[action: string, capability: Capability]> = [
  ['home', 'input_button'], ['lock', 'input_button'], ['appearance', 'appearance'], ['devices', 'device_list'],
]

export function barMarkup(icon: IconRenderer): string {
  const button = (action: string, name: IconName, title: string, extra = '') =>
    `<button type="button" class="smv-icon" data-smv="${action}" title="${title}" aria-label="${title}"${extra}>`
    + `${icon(name)}</button>`
  return `<div class="smv-bar" part="bar">
      <span class="smv-name" data-smv-name>iOS Simulator</span>
      <span class="smv-state" data-smv-state data-state="stopped">${STATE_LABELS.stopped}</span>
      <span class="smv-mode" data-smv-mode hidden>View only</span>
      <span class="smv-spacer"></span>
      ${button('home', 'home', 'Home')}
      ${button('lock', 'lock', 'Lock')}
      ${button('appearance', 'moon', 'Dark appearance', ' aria-pressed="false"')}
      ${button('devices', 'devices', 'Choose a simulator', ' aria-haspopup="menu"')}
      ${button('place', 'undock', 'Undock into a window')}
      <a class="smv-icon" data-smv-page target="_blank" rel="noopener" title="Open on its own page"
         aria-label="Open on its own page">${icon('page')}</a>
      ${button('stop', 'power', 'Let the simulator go')}
      ${button('close', 'close', 'Close')}
    </div>`
}

export interface ControlsOptions {
  el: HTMLElement
  picker: HTMLElement
  transport: SimMirrorTransport
  stream: ViewerStream
  status: StatusView
  input: ViewerInput
  icon: IconRenderer
  placement(): Placement
  onPlace?(next: 'dock' | 'window'): void
  pageHref?: string
  onClose?(): void
}

export interface Controls {
  paintPlacement(): void
  applyHello(hello: ServerHello | null): void
}

export function createControls(options: ControlsOptions): Controls {
  const { el, picker, transport, stream, status, input, icon } = options
  const q = <T extends Element>(selector: string) => el.querySelector<T>(selector)!
  const placeButton = q<HTMLButtonElement>('[data-smv="place"]')
  const pageLink = q<HTMLAnchorElement>('[data-smv-page]')
  const closeButton = q<HTMLButtonElement>('[data-smv="close"]')
  const appearanceButton = q<HTMLButtonElement>('[data-smv="appearance"]')
  let dark = false

  function closePicker(): void {
    picker.hidden = true
    picker.innerHTML = ''
  }

  async function togglePicker(): Promise<void> {
    if (!picker.hidden) return closePicker()
    picker.hidden = false
    picker.innerHTML = '<p class="smv-picker-note">Looking for simulators…</p>'
    let choices: DeviceChoice[]
    try {
      choices = await transport.devices()
    } catch (err) {
      if (!picker.hidden) {
        const message = (err as Error).message || 'The simulators could not be listed.'
        picker.innerHTML = `<p class="smv-picker-note">${escapeHTML(message)}</p>`
      }
      return
    }
    if (picker.hidden) return
    const rows = choices.map((choice) =>
      `<button type="button" role="menuitemradio" aria-checked="${choice.udid === stream.device?.udid}"`
      + ` data-smv-udid="${escapeHTML(choice.udid)}">${escapeHTML(choice.name)}<span>${escapeHTML(choice.runtime)}</span></button>`)
    picker.innerHTML = (rows.join('') || '<p class="smv-picker-note">No iOS simulators on this Mac.</p>')
      + '<button type="button" role="menuitem" data-smv="shutdown">Shut down this device</button>'
  }

  async function pick(udid: string): Promise<void> {
    closePicker()
    if (udid === stream.device?.udid) return
    try {
      await transport.choose(udid)
    } catch (err) {
      status.say((err as Error).message || 'That simulator could not be used.', 'retry')
      return
    }
    stream.close()
    status.say('Switching simulators…')
    await stream.connect()
  }

  function letGo(shutdown: boolean): void {
    closePicker()
    input.flushTyped()
    stream.close()
    status.stopBootTimer()
    status.say(shutdown ? 'The device is shut down.' : 'The simulator is let go; its device keeps running for next time.',
      'start')
    void transport.stop(shutdown).catch(() => undefined)
  }

  function paintAppearance(): void {
    appearanceButton.setAttribute('aria-pressed', String(dark))
    appearanceButton.title = dark ? 'Light appearance' : 'Dark appearance'
    appearanceButton.setAttribute('aria-label', appearanceButton.title)
    appearanceButton.innerHTML = icon(dark ? 'sun' : 'moon')
  }

  function paintPlacement(): void {
    const placement = options.placement()
    el.dataset.placement = placement
    const undock = placement === 'dock'
    placeButton.hidden = placement === 'page' || !options.onPlace
    placeButton.title = undock ? 'Undock into a window' : 'Dock beside the page'
    placeButton.setAttribute('aria-label', placeButton.title)
    placeButton.innerHTML = icon(undock ? 'undock' : 'dock')
    pageLink.hidden = placement === 'page' || !options.pageHref
    pageLink.setAttribute('href', options.pageHref ?? '')
    closeButton.hidden = !options.onClose
  }

  el.addEventListener('click', (event) => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-smv], [data-smv-udid]')
    if (!target) return
    if (target.dataset.smvUdid !== undefined) {
      void pick(target.dataset.smvUdid)
      return
    }
    const action = target.dataset.smv
    if (action === 'home' || action === 'lock') {
      stream.send({ type: 'button', name: action })
    } else if (action === 'appearance') {
      if (stream.send({ type: 'appearance', mode: dark ? 'light' : 'dark' })) {
        dark = !dark
        paintAppearance()
      }
    } else if (action === 'devices') {
      void togglePicker()
    } else if (action === 'shutdown' || action === 'stop') {
      letGo(action === 'shutdown')
    } else if (action === 'place') {
      options.onPlace?.(options.placement() === 'dock' ? 'window' : 'dock')
    } else if (action === 'start') {
      stream.restart()
    } else if (action === 'close') {
      options.onClose?.()
    }
  })

  return {
    paintPlacement,
    applyHello(hello) {
      for (const [action, capability] of NEEDS) {
        q<HTMLButtonElement>(`[data-smv="${action}"]`).hidden = hello !== null && !hello.capabilities.includes(capability)
      }
    },
  }
}
