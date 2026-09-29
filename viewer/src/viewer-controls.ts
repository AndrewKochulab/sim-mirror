// SPDX-License-Identifier: Apache-2.0
/**
 * The viewer's toolbar: Home and Lock, the appearance, recording (`recording-control.ts`), how the device looks for a
 * demo (`display-menu.ts`), the device picker, where the view is placed, letting the device go, and closing. A button
 * the device's connector cannot press is not offered once the server has said what it can do, nor one the transport
 * has no call for.
 *
 * The picker is a menu (`popover.ts`): Escape closes it and gives focus back to its button, and the arrow keys walk its
 * rows. It lists this Mac's simulators, then the iPhones and iPads connected to it -- each with how it is connected, and
 * what stands in its way. Settings open in a panel of their own (`settings-panel.ts`), offered only by a transport that
 * reads settings. The "View only" badge says what would let the device be touched: a real device's is Set up touch
 * (`touch-setup.ts`), and a simulator's opens the settings where its connector is chosen.
 */
import { createDisplayMenu, type DisplayMenu } from './display-menu'
import { escapeHTML } from './escape'
import type { IconName, IconRenderer } from './icons'
import { createNote } from './note'
import { createPopover, type Layers } from './popover'
import { createRecordingControl, type RecordingControl } from './recording-control'
import { createSettingsPanel, type SettingsPanel } from './settings-panel'
import { createTouchSetup, type TouchSetupControl } from './touch-setup'
import type { Capability, Device, DeviceChoice, ServerHello } from './protocol.generated'
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
      <span class="smv-kind" data-smv-kind hidden></span>
      <span class="smv-state" data-smv-state data-state="stopped">${STATE_LABELS.stopped}</span>
      <button type="button" class="smv-mode" data-smv="mode" data-smv-mode hidden>View only</button>
      <span class="smv-app" data-smv-app hidden></span>
      <span class="smv-rec" data-smv-rec hidden></span>
      <span class="smv-spacer"></span>
      ${button('home', 'home', 'Home')}
      ${button('lock', 'lock', 'Lock')}
      ${button('appearance', 'moon', 'Dark appearance', ' aria-pressed="false"')}
      ${button('record', 'record', 'Record the screen', ' aria-pressed="false" hidden')}
      ${button('display', 'contrast', 'Display', ' aria-haspopup="menu" aria-expanded="false" hidden')}
      ${button('devices', 'devices', 'Choose a device', ' aria-haspopup="menu" aria-expanded="false"')}
      ${button('settings', 'settings', 'Settings', ' aria-haspopup="dialog" aria-expanded="false" hidden')}
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
  /** The Display menu's panel. */
  display: HTMLElement
  /** The note over the screen. */
  note: HTMLElement
  /** Where the settings panel is drawn. */
  settingsPanel: HTMLElement
  transport: SimMirrorTransport
  stream: ViewerStream
  status: StatusView
  input: ViewerInput
  icon: IconRenderer
  layers: Layers
  placement(): Placement
  onPlace?(next: 'dock' | 'window'): void
  pageHref?: string
  onClose?(): void
}

export interface Controls {
  paintPlacement(): void
  applyHello(hello: ServerHello | null): void
  /** How the device stands: what it is, and whether it is being recorded. */
  applyDevice(device: Device | null): void
  destroy(): void
}

/** A device the picker lists, grouped by kind: simulators first. */
function choiceRow(choice: DeviceChoice, current: string | undefined): string {
  const physical = choice.kind === 'physical'
  const how = physical ? ` · ${choice.connection === 'usb' ? 'USB' : choice.connection ? 'Wi-Fi' : 'Not connected'}` : ''
  const usable = choice.usable !== false
  const why = choice.detail ? ` title="${escapeHTML(choice.detail)}"` : ''
  return `<button type="button" role="menuitemradio" aria-checked="${choice.udid === current}"`
    + `${usable ? '' : ' aria-disabled="true"'}${why} data-smv-udid="${escapeHTML(choice.udid)}">`
    + `${escapeHTML(choice.name)}<span>${escapeHTML(choice.runtime + how)}`
    + `${choice.detail ? ` · ${escapeHTML(choice.detail)}` : ''}</span></button>`
}

export function pickerMarkup(choices: DeviceChoice[], current: Device | null): string {
  const groups: Array<[label: string, rows: DeviceChoice[]]> = [
    ['Simulators', choices.filter((choice) => choice.kind !== 'physical')],
    ['iPhones and iPads', choices.filter((choice) => choice.kind === 'physical')],
  ]
  const listed = groups.filter(([, rows]) => rows.length)
  const body = listed.map(([label, rows]) =>
    (listed.length > 1 ? `<p class="smv-picker-group" role="presentation">${label}</p>` : '')
    + rows.map((choice) => choiceRow(choice, current?.udid)).join('')).join('')
  // A real device is never shut down: it is only let go.
  const shutdown = current?.kind === 'physical'
    ? '' : '<button type="button" role="menuitem" data-smv="shutdown">Shut down this device</button>'
  return (body || '<p class="smv-picker-note">No simulators or devices on this Mac.</p>') + shutdown
}

export function createControls(options: ControlsOptions): Controls {
  const { el, picker, transport, stream, status, input, icon, layers } = options
  const q = <T extends Element>(selector: string) => el.querySelector<T>(selector)!
  const note = createNote(options.note, icon)
  const recordButton = q<HTMLButtonElement>('[data-smv="record"]')
  const { startRecording, stopRecording, recordingFile, changeDevice } = transport
  // Bound, since a host's transport may be an object whose methods use `this`.
  const recording: RecordingControl | null = startRecording && stopRecording && recordingFile
    ? createRecordingControl({
      button: recordButton, clock: q('[data-smv-rec]'), noteEl: options.note, note, icon,
      transport: {
        startRecording: startRecording.bind(transport),
        stopRecording: stopRecording.bind(transport),
        recordingFile: recordingFile.bind(transport),
      },
    })
    : null
  const display: DisplayMenu | null = changeDevice
    ? createDisplayMenu({
      root: el, toggle: q('[data-smv="display"]'), panel: options.display, layers, note,
      changeDevice: changeDevice.bind(transport),
    })
    : null
  let hello: ServerHello | null = null
  const placeButton = q<HTMLButtonElement>('[data-smv="place"]')
  const pageLink = q<HTMLAnchorElement>('[data-smv-page]')
  const closeButton = q<HTMLButtonElement>('[data-smv="close"]')
  const appearanceButton = q<HTMLButtonElement>('[data-smv="appearance"]')
  const devicesButton = q<HTMLButtonElement>('[data-smv="devices"]')
  const settingsButton = q<HTMLButtonElement>('[data-smv="settings"]')
  let dark = false

  const menu = createPopover({
    root: el, toggle: devicesButton, panel: picker, layers, onClose: () => { picker.innerHTML = '' },
  })

  const closePicker = () => menu.close('toggle')

  const { settings: readSettings, changeSettings } = transport
  const settings: SettingsPanel | null = readSettings && changeSettings
    ? createSettingsPanel({
      root: el, toggle: settingsButton, panel: options.settingsPanel, layers, icon,
      // Bound, since a host's transport may be an object whose methods use `this`.
      transport: { settings: readSettings.bind(transport), changeSettings: changeSettings.bind(transport) },
    })
    : null
  settingsButton.hidden = settings === null

  const { touch, setUpTouch } = transport
  const touchSetup: TouchSetupControl | null = touch && setUpTouch
    ? createTouchSetup({
      noteEl: options.note, note,
      transport: { touch: touch.bind(transport), setUpTouch: setUpTouch.bind(transport) },
      openSettings: settings ? () => void settings.show('real_devices') : undefined,
    })
    : null

  async function togglePicker(): Promise<void> {
    if (menu.isOpen) return closePicker()
    menu.open()
    picker.innerHTML = '<p class="smv-picker-note">Looking for devices…</p>'
    let choices: DeviceChoice[]
    try {
      choices = await transport.devices()
    } catch (err) {
      if (menu.isOpen) {
        const message = (err as Error).message || 'The devices could not be listed.'
        picker.innerHTML = `<p class="smv-picker-note">${escapeHTML(message)}</p>`
      }
      return
    }
    if (!menu.isOpen) return
    picker.innerHTML = pickerMarkup(choices, stream.device)
    menu.focusItem('checked')
  }

  async function pick(row: HTMLElement): Promise<void> {
    if (row.getAttribute('aria-disabled') === 'true') return
    const udid = row.dataset.smvUdid!
    closePicker()
    if (udid === stream.device?.udid) return
    try {
      await transport.choose(udid)
    } catch (err) {
      status.say((err as Error).message || 'That device could not be used.', 'retry')
      return
    }
    stream.close()
    status.say('Switching devices…')
    await stream.connect()
  }

  function letGo(shutdown: boolean): void {
    closePicker()
    input.flushTyped()
    stream.close()
    status.stopBootTimer()
    const physical = stream.device?.kind === 'physical'
    status.say(shutdown ? 'The device is shut down.'
      : physical ? 'The device is let go, as it was.' : 'The simulator is let go; its device keeps running for next time.',
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
      void pick(target)
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
      settings?.close()
      void togglePicker()
    } else if (action === 'settings') {
      closePicker()
      void settings?.toggle()
    } else if (action === 'mode') {
      closePicker()
      const physical = stream.device?.kind === 'physical'
      if (physical && touchSetup) void touchSetup.open()
      // Where what would let the device be touched is set.
      else void settings?.show(physical ? 'real_devices' : 'connectors')
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
    applyHello(next) {
      hello = next
      for (const [action, capability] of NEEDS) {
        q<HTMLButtonElement>(`[data-smv="${action}"]`).hidden = next !== null && !next.capabilities.includes(capability)
      }
      recordButton.hidden = recording === null || !next?.capabilities.includes('record')
      display?.offer(next?.capabilities ?? [], stream.device?.kind === 'physical')
    },
    applyDevice(device) {
      const stop = q<HTMLButtonElement>('[data-smv="stop"]')
      stop.title = device?.kind === 'physical' ? 'Let the device go' : 'Let the simulator go'
      stop.setAttribute('aria-label', stop.title)
      recording?.applyDevice(device)
      display?.offer(hello?.capabilities ?? [], device?.kind === 'physical')
    },
    destroy() {
      menu.destroy()
      settings?.destroy()
      recording?.destroy()
      display?.destroy()
      touchSetup?.destroy()
      note.destroy()
    },
  }
}
