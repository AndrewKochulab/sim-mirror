// SPDX-License-Identifier: Apache-2.0
/**
 * The viewer's toolbar: the device's buttons -- Home and Lock, and an Android device's Back, Recents and volume --,
 * the appearance, recording and how big it is kept (`recording-control.ts`), how the device looks for a demo
 * (`display-menu.ts`), the device picker, where the view is placed, letting the device go, and closing. A button the
 * device cannot press is not offered once the server has said what it can do, nor one the transport has no call for.
 *
 * The picker is a menu (`popover.ts`): Escape closes it and gives focus back to its button, and the arrow keys walk its
 * rows. It lists this Mac's simulators, the iPhones and iPads connected to it, then the Android emulators and phones --
 * each with how it is connected, and what stands in its way. Settings open in a panel of their own
 * (`settings-panel.ts`), offered only by a transport that reads settings. The badge beside the name says what would let
 * the device be touched, or touched quickly: a real device's and an Android device's is Set up touch
 * (`touch-setup.ts`), and a simulator's opens the settings where its connector is chosen.
 */
import { buttonsOf, connectionLabel, deviceNoun, isReal } from './device-kind'
import { createDisplayMenu, type DisplayMenu } from './display-menu'
import { escapeHTML } from './escape'
import type { IconName, IconRenderer } from './icons'
import { createNote } from './note'
import { createPopover, type Layers } from './popover'
import { createRecordingControl, type RecordingControl } from './recording-control'
import { createSettingsPanel, type SettingsPanel } from './settings-panel'
import { createTouchSetup, type TouchSetupControl } from './touch-setup'
import type { Capability, Device, DeviceChoice, PanelButton, ServerHello } from './protocol.generated'
import { STATE_LABELS, type StatusView } from './status-view'
import type { SimMirrorTransport } from './transport'
import type { ViewerInput } from './viewer-input'
import type { ViewerStream } from './viewer-stream'

export type Placement = 'dock' | 'window' | 'page'

/** The buttons that need the connector to be able to do something. */
const NEEDS: ReadonlyArray<[action: string, capability: Capability]> = [
  ['appearance', 'appearance'], ['devices', 'device_list'],
]

/** The device's own buttons the toolbar offers, in its order, with what each is called. */
export const BAR_BUTTONS: ReadonlyArray<[button: PanelButton & IconName, title: string]> = [
  ['back', 'Back'], ['home', 'Home'], ['app_switch', 'Recent apps'], ['lock', 'Lock'],
  ['volume_down', 'Volume down'], ['volume_up', 'Volume up'],
]
const PRESSED = new Set<string>(BAR_BUTTONS.map(([button]) => button))

export function barMarkup(icon: IconRenderer): string {
  const button = (action: string, name: IconName, title: string, extra = '') =>
    `<button type="button" class="smv-icon" data-smv="${action}" title="${title}" aria-label="${title}"${extra}>`
    + `${icon(name)}</button>`
  // Home and Lock until the device says which it has: what a viewer of an older server always showed.
  const pressed = BAR_BUTTONS.map(([name, title]) =>
    button(name, name, title, name === 'home' || name === 'lock' ? '' : ' hidden')).join('\n      ')
  return `<div class="smv-bar" part="bar">
      <span class="smv-name" data-smv-name>iOS Simulator</span>
      <span class="smv-kind" data-smv-kind hidden></span>
      <span class="smv-state" data-smv-state data-state="stopped">${STATE_LABELS.stopped}</span>
      <button type="button" class="smv-mode" data-smv="mode" data-smv-mode hidden>View only</button>
      <span class="smv-app" data-smv-app hidden></span>
      <span class="smv-rec" data-smv-rec hidden></span>
      <span class="smv-spacer"></span>
      ${pressed}
      ${button('appearance', 'moon', 'Dark appearance', ' aria-pressed="false"')}
      ${button('record', 'record', 'Record the screen', ' aria-pressed="false" hidden')}
      <button type="button" class="smv-icon smv-size" data-smv="record-size" title="Recording size: Full size"
         aria-label="Recording size: Full size" aria-haspopup="menu" aria-expanded="false" hidden></button>
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
  /** The recording size menu's panel. */
  recordSize: HTMLElement
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
  /** How the device stands: what it is, whether it is dark, and whether it is being recorded. */
  applyDevice(device: Device | null): void
  /** Say something over the screen without taking it away: input the device did not take. */
  tell(message: string): void
  destroy(): void
}

/** How a device the picker lists reaches this Mac: a real device by cable or network, an emulator on it. */
function reached(choice: DeviceChoice): string {
  if (choice.kind === 'physical') return ` · ${connectionLabel(choice.connection)}`
  if (choice.kind === 'android') return ` · ${choice.connection ? connectionLabel(choice.connection) : 'Emulator'}`
  return ''
}

/** A device the picker lists, grouped by kind: simulators first. */
function choiceRow(choice: DeviceChoice, current: string | undefined): string {
  const how = reached(choice)
  const usable = choice.usable !== false
  const why = choice.detail ? ` title="${escapeHTML(choice.detail)}"` : ''
  return `<button type="button" role="menuitemradio" aria-checked="${choice.udid === current}"`
    + `${usable ? '' : ' aria-disabled="true"'}${why} data-smv-udid="${escapeHTML(choice.udid)}">`
    + `${escapeHTML(choice.name)}<span>${escapeHTML(choice.runtime + how)}`
    + `${choice.detail ? ` · ${escapeHTML(choice.detail)}` : ''}</span></button>`
}

export function pickerMarkup(choices: DeviceChoice[], current: Device | null): string {
  const groups: Array<[label: string, rows: DeviceChoice[]]> = [
    // An older server says no kind: its choices are simulators.
    ['Simulators', choices.filter((choice) => choice.kind !== 'physical' && choice.kind !== 'android')],
    ['iPhones and iPads', choices.filter((choice) => choice.kind === 'physical')],
    ['Android', choices.filter((choice) => choice.kind === 'android')],
  ]
  const listed = groups.filter(([, rows]) => rows.length)
  const body = listed.map(([label, rows]) =>
    (listed.length > 1 ? `<p class="smv-picker-group" role="presentation">${label}</p>` : '')
    + rows.map((choice) => choiceRow(choice, current?.udid)).join('')).join('')
  // A real device -- an iPhone, an Android phone -- is never shut down: it is only let go.
  const shutdown = isReal(current)
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
      button: recordButton, clock: q('[data-smv-rec]'), noteEl: options.note, note, icon, root: el, layers,
      sizeToggle: q('[data-smv="record-size"]'), sizePanel: options.recordSize,
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
      openSettings: settings
        ? () => void settings.show(stream.device?.kind === 'android' ? 'android' : 'real_devices') : undefined,
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
      // The device in use is still in use: its screen stays, and the note says why the other could not be.
      note.sayText((err as Error).message || 'That device could not be used.')
      return
    }
    stream.close()
    status.say('Switching devices…')
    await stream.connect()
  }

  function letGo(shutdown: boolean): void {
    closePicker()
    input.flushTyped()
    const noun = deviceNoun(stream.device)
    stream.end(shutdown ? 'The device is shut down.'
      : noun === 'device' ? 'The device is let go, as it was.'
        : noun === 'emulator' ? 'The emulator is let go; it keeps running for next time.'
          : 'The simulator is let go; its device keeps running for next time.',
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
    if (action !== undefined && PRESSED.has(action)) {
      stream.send({ type: 'button', name: action as PanelButton })
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
      const kind = stream.device?.kind
      if (kind !== undefined && kind !== 'simulator' && touchSetup) void touchSetup.open()
      // Where what would let the device be touched is set.
      else void settings?.show(kind === 'physical' ? 'real_devices' : kind === 'android' ? 'android' : 'connectors')
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

  /** The device's buttons it can press: those it has, while its connector can press any. */
  function paintButtons(device: Device | null): void {
    const pressable = hello === null || hello.capabilities.includes('input_button')
    const has = buttonsOf(device)
    for (const [name] of BAR_BUTTONS) q<HTMLButtonElement>(`[data-smv="${name}"]`).hidden = !pressable || !has.includes(name)
  }

  /** A simulator's motion is not the viewer's to reduce; any other device's is. */
  const reducesMotion = (device: Device | null) => device !== null && device.kind !== 'simulator'

  return {
    paintPlacement,
    applyHello(next) {
      hello = next
      for (const [action, capability] of NEEDS) {
        q<HTMLButtonElement>(`[data-smv="${action}"]`).hidden = next !== null && !next.capabilities.includes(capability)
      }
      paintButtons(stream.device)
      recording?.offer(next !== null && next.capabilities.includes('record'))
      display?.offer(next?.capabilities ?? [], reducesMotion(stream.device))
    },
    applyDevice(device) {
      // The device says whether it is dark -- after a reconnect, a switch, or another viewer's or an agent's change.
      // An older server does not, and the button keeps what this viewer last set.
      const appearance = device?.appearance
      if (appearance === 'dark' || appearance === 'light') {
        dark = appearance === 'dark'
        paintAppearance()
      }
      const stop = q<HTMLButtonElement>('[data-smv="stop"]')
      stop.title = `Let the ${deviceNoun(device)} go`
      stop.setAttribute('aria-label', stop.title)
      paintButtons(device)
      recording?.applyDevice(device)
      display?.offer(hello?.capabilities ?? [], reducesMotion(device))
    },
    tell: (message) => note.sayText(message),
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
