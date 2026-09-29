// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FakeSocket } from '../test-support/fake-socket'
import { settingsView } from '../test-support/settings'
import {
  CLOCK, DEVICE, FULL, HELLO, VIEW_ONLY, flush, live, said, setup, socket,
} from '../test-support/viewer-harness'
import { TEXT_SIZES, createDisplayMenu } from './display-menu'
import { createNote } from './note'
import { createLayers } from './popover'
import type { Capability, Device, Recording, RecordingState } from './protocol.generated'
import { createRecordingControl, download, elapsed, sizeOf } from './recording-control'
import { lucideSvg } from './icons'

const PHONE: Device = {
  ...DEVICE, udid: '00008120-0011223344556677', name: 'Test iPhone', runtime: 'iOS 26.3 · iPhone 14 Pro',
  kind: 'physical', connection: 'usb', created: false, booted_by_us: false,
}
const UNDER_WAY: RecordingState = { id: 'r1', since_ms: 0, by: 'person', max_ms: 300000 }
const KEPT: Recording = {
  id: 'r1', device: 'Test iPhone', started_at: '2026-09-29T00:45:15Z', duration_ms: 10_800, notes: ['kept as recorded'],
  files: [
    { name: 'r1.mp4', path: '/r/r1.mp4', format: 'mp4', bytes: 4_300_000, width: 900, height: 1952 },
    { name: 'r1.gif', path: '/r/r1.gif', format: 'gif', bytes: 40_000, width: 600, height: 1302 },
  ],
}

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 1206, height: 2622, close: vi.fn() })))
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as never
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('the picker', () => {
  const choices = [
    { udid: 'U1', name: 'iPhone 17 Pro', runtime: 'iOS 26.5', state: 'Booted', created: true, kind: 'simulator',
      connection: null, detail: null, usable: true },
    { udid: PHONE.udid, name: 'Test iPhone', runtime: 'iOS 26.3 · iPhone 14 Pro', state: 'Connected', created: false,
      kind: 'physical', connection: 'usb', detail: null, usable: true },
    { udid: 'P2', name: 'Second iPhone', runtime: 'iOS 27.0', state: 'Connected', created: false, kind: 'physical',
      connection: 'network', detail: 'Locked', usable: true },
    { udid: 'P3', name: 'Old iPad', runtime: 'iPadOS 18', state: 'Disconnected', created: false, kind: 'physical',
      connection: null, detail: 'Not connected', usable: false },
  ]

  it('groups simulators and real devices, says how each is connected, and will not pick one it cannot use', async () => {
    const { $, transport } = await live({ transport: { devices: vi.fn(async () => choices) } })
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    await flush()
    const picker = $<HTMLElement>('[data-smv-picker]')
    expect([...picker.querySelectorAll('.smv-picker-group')].map((group) => group.textContent))
      .toEqual(['Simulators', 'iPhones and iPads'])
    const rows = [...picker.querySelectorAll<HTMLButtonElement>('[data-smv-udid]')]
    expect(rows.map((row) => row.querySelector('span')!.textContent)).toEqual([
      'iOS 26.5', 'iOS 26.3 · iPhone 14 Pro · USB', 'iOS 27.0 · Wi-Fi · Locked', 'iPadOS 18 · Not connected · Not connected',
    ])
    expect(rows[3].getAttribute('aria-disabled')).toBe('true')
    expect(rows[3].title).toBe('Not connected')
    expect(picker.querySelector('[data-smv="shutdown"]')).not.toBeNull()
    rows[3].click()
    await flush()
    expect(transport.choose).not.toHaveBeenCalled()
    expect(picker.hidden).toBe(false)
  })

  it('lists one kind without a heading, and a real device is let go -- never shut down', async () => {
    const phones = choices.filter((choice) => choice.kind === 'physical')
    const { $, transport } = await live({ transport: { devices: vi.fn(async () => phones) } })
    socket().message({ type: 'status', ...PHONE })
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    await flush()
    const picker = $<HTMLElement>('[data-smv-picker]')
    expect(picker.querySelector('.smv-picker-group')).toBeNull()
    expect(picker.querySelector('[data-smv="shutdown"]')).toBeNull()
    $<HTMLButtonElement>('[data-smv="stop"]').click()
    expect(transport.stop).toHaveBeenCalledWith(false)
  })

  it('says a real device is let go as it was', async () => {
    const { $, view } = await live()
    socket().message({ type: 'status', ...PHONE })
    $<HTMLButtonElement>('[data-smv="stop"]').click()
    expect(said(view)).toContain('The device is let go, as it was.')
  })
})

describe('a real device in the bar', () => {
  it('is named a real device, with how it is connected, and a stopped one is a device', async () => {
    const { $, view } = await live()
    const kind = $<HTMLElement>('[data-smv-kind]')
    expect(kind.hidden).toBe(true)
    socket().message({ type: 'status', ...PHONE })
    expect(kind.hidden).toBe(false)
    expect(kind.textContent).toBe('Real device · USB')
    socket().message({ type: 'status', ...PHONE, connection: 'network' })
    expect(kind.textContent).toBe('Real device · Wi-Fi')
    socket().message({ type: 'status', ...PHONE, state: 'failed', reason: null })
    expect(said(view)).toContain('The device could not start.')
    socket().message({ type: 'status', ...PHONE, state: 'stalled', reason: null })
    expect(said(view)).toContain('The device stopped answering. Reconnecting…')
    socket().message({ type: 'status', ...PHONE, state: 'stopped' })
    expect(said(view)).toContain('The device is stopped.')
    expect($<HTMLButtonElement>('[data-smv-empty] [data-smv="start"]').textContent).toBe('Show the device')
    expect($<HTMLButtonElement>('[data-smv="stop"]').title).toBe('Let the device go')
    socket().message({ type: 'status', ...DEVICE, state: 'stopped' })
    expect($<HTMLButtonElement>('[data-smv-empty] [data-smv="start"]').textContent).toBe('Start the simulator')
    expect($<HTMLButtonElement>('[data-smv="stop"]').title).toBe('Let the simulator go')
  })

  it('opens the settings where what would let it be touched is set, from the view-only badge', async () => {
    const settings = vi.fn(async () => settingsView({
      sections: [
        { id: '', title: 'General', doc: '' },
        { id: 'connectors', title: 'Connectors', doc: '' },
        { id: 'real_devices', title: 'Real devices', doc: '' },
      ],
    }))
    const { $ } = await live({ transport: { settings, changeSettings: vi.fn() } }, {
      ...HELLO, connector: 'iphone', capabilities: VIEW_ONLY,
      fallback_reason: 'Touching and reading it needs WebDriverAgent: `sim-mirror wda setup`.',
    })
    socket().message({ type: 'status', ...PHONE })
    const mode = $<HTMLButtonElement>('[data-smv="mode"]')
    expect(mode.hidden).toBe(false)
    expect(mode.title).toContain('WebDriverAgent')
    mode.click()
    await flush()
    const selected = () => $<HTMLElement>('[data-smv-settings] [aria-selected="true"]').textContent
    expect(selected()).toBe('Real devices')
    socket().message({ type: 'status', ...DEVICE })
    mode.click()
    await flush()
    expect(selected()).toBe('Connectors')
    mode.click()
    await flush()
    expect(settings).toHaveBeenCalledTimes(1)
  })

  it('goes nowhere it cannot while the settings are still loading, or to a section there is none of', async () => {
    let answer: (view: ReturnType<typeof settingsView>) => void = () => undefined
    const settings = vi.fn(() => new Promise<ReturnType<typeof settingsView>>((resolve) => { answer = resolve }))
    const { $ } = await live({ transport: { settings, changeSettings: vi.fn() } }, { ...HELLO, capabilities: VIEW_ONLY })
    socket().message({ type: 'status', ...PHONE })
    const mode = $<HTMLButtonElement>('[data-smv="mode"]')
    mode.click()
    mode.click()
    answer(settingsView())
    await flush()
    mode.click()
    await flush()
    expect($<HTMLElement>('[data-smv-settings] [aria-selected="true"]').textContent).toBe('General')
  })
})

describe('recording', () => {
  const recordingCalls = () => ({
    startRecording: vi.fn(async () => UNDER_WAY),
    stopRecording: vi.fn(async () => KEPT),
    recordingFile: vi.fn(async () => new Blob(['movie'])),
  })

  it('is offered only where the device can be recorded and the transport can ask', async () => {
    const without = await live()
    expect(without.$<HTMLButtonElement>('[data-smv="record"]').hidden).toBe(true)
    document.body.innerHTML = ''
    const viewOnly = await live({ transport: recordingCalls() }, {
      ...HELLO, capabilities: FULL.filter((capability) => capability !== 'record'),
    })
    expect(viewOnly.$<HTMLButtonElement>('[data-smv="record"]').hidden).toBe(true)
  })

  it('starts, counts, stops, and offers each file kept to save', async () => {
    vi.useFakeTimers(CLOCK)
    const calls = recordingCalls()
    const { $ } = await live({ transport: calls })
    const button = $<HTMLButtonElement>('[data-smv="record"]')
    const clock = $<HTMLElement>('[data-smv-rec]')
    expect(button.hidden).toBe(false)
    expect(button.title).toBe('Record the screen')
    button.click()
    await flush()
    expect(calls.startRecording).toHaveBeenCalledOnce()
    expect(button.getAttribute('aria-pressed')).toBe('true')
    expect(button.title).toBe('Stop recording (started by person)')
    expect(clock.textContent).toBe('REC 0:00')
    vi.advanceTimersByTime(72_000)
    expect(clock.textContent).toBe('REC 1:12')
    button.click()
    await flush()
    const note = $<HTMLElement>('[data-smv-note]')
    expect(note.hidden).toBe(false)
    expect(note.textContent).toContain('Recorded 0:10 of Test iPhone.')
    expect(note.textContent).toContain('kept as recorded')
    expect([...note.querySelectorAll('[data-smv-save]')].map((save) => save.textContent))
      .toEqual(['Save MP4 · 4.3 MB', 'Save GIF · 40 KB'])
    expect(clock.hidden).toBe(true)
    const created = vi.fn(() => 'blob:r1')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() }))
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    note.querySelector<HTMLButtonElement>('[data-smv-save="r1.mp4"]')!.click()
    await flush()
    expect(calls.recordingFile).toHaveBeenCalledWith('r1.mp4')
    expect(clicked).toHaveBeenCalledOnce()
    vi.advanceTimersByTime(60_000)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:r1')
    note.querySelector<HTMLButtonElement>('[data-smv-note-close]')!.click()
    expect(note.hidden).toBe(true)
  })

  it('shows one an agent started, and says what stopped a recording or a save', async () => {
    const calls = recordingCalls()
    calls.stopRecording.mockRejectedValueOnce(new Error('nothing is being recorded'))
    calls.recordingFile.mockRejectedValueOnce(new Error('there is no recording'))
    calls.startRecording.mockRejectedValueOnce(new Error(''))
    const { $ } = await live({ transport: calls })
    const button = $<HTMLButtonElement>('[data-smv="record"]')
    const note = $<HTMLElement>('[data-smv-note]')
    socket().message({ type: 'status', ...DEVICE, recording: { ...UNDER_WAY, by: 'Claude · notes' } })
    expect(button.title).toBe('Stop recording (started by Claude · notes)')
    socket().message({ type: 'status', ...DEVICE, recording: { ...UNDER_WAY, by: 'Claude · notes' } })
    button.click()
    await flush()
    expect(note.textContent).toContain('nothing is being recorded')
    socket().message({ type: 'status', ...DEVICE, recording: null })
    expect(button.getAttribute('aria-pressed')).toBe('false')
    button.click()
    await flush()
    expect(note.textContent).toContain('The recording could not be made.')
    button.click()
    await flush()
    button.click()
    await flush()
    note.querySelector<HTMLButtonElement>('[data-smv-save="r1.gif"]')!.click()
    await flush()
    expect(note.textContent).toContain('there is no recording')
    calls.recordingFile.mockRejectedValueOnce(new Error(''))
    button.click()
    await flush()
    button.click()
    await flush()
    note.querySelector<HTMLButtonElement>('[data-smv-save="r1.gif"]')!.click()
    await flush()
    expect(note.textContent).toContain('r1.gif could not be saved.')
  })

  it('lets go of its clock when the viewer goes, and hides while the viewer is not live', async () => {
    vi.useFakeTimers(CLOCK)
    const calls = recordingCalls()
    const { $, view } = await live({ transport: calls })
    $<HTMLButtonElement>('[data-smv="record"]').click()
    await flush()
    view.setActive(false)
    await flush()
    view.destroy()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('counts time, sizes files and saves as a person reads them', () => {
    expect([elapsed(-5), elapsed(7_900), elapsed(750_000)]).toEqual(['0:00', '0:07', '12:30'])
    expect([sizeOf(10), sizeOf(40_000), sizeOf(4_300_000)]).toEqual(['1 KB', '40 KB', '4.3 MB'])
    const saved: string[] = []
    const button = document.createElement('button')
    const noteEl = document.createElement('div')
    const control = createRecordingControl({
      button, clock: document.createElement('span'), noteEl, note: createNote(noteEl, lucideSvg), icon: lucideSvg,
      transport: { startRecording: vi.fn(), stopRecording: vi.fn(), recordingFile: vi.fn() },
      save: (_file, name) => saved.push(name),
    })
    control.applyDevice(null)
    control.destroy()
    expect(typeof download).toBe('function')
    expect(saved).toEqual([])
  })
})

describe('the Display menu', () => {
  const display = (said = 'done') => ({ changeDevice: vi.fn(async () => said) })
  const DISPLAY: Capability[] = [...VIEW_ONLY, 'status_bar', 'accessibility', 'input_touch']

  async function opened(calls = display(), device: Device = PHONE) {
    const rig = await live({ transport: calls }, { ...HELLO, capabilities: DISPLAY })
    socket().message({ type: 'status', ...device })
    const toggle = rig.$<HTMLButtonElement>('[data-smv="display"]')
    toggle.click()
    const panel = rig.$<HTMLElement>('[data-smv-display]')
    const row = (id: string) => panel.querySelector<HTMLButtonElement>(`[data-smv-display="${id}"]`)
    return { ...rig, calls, toggle, panel, row }
  }

  it('offers only the changes the device can make, and none without a transport that asks', async () => {
    const plain = await live({}, { ...HELLO, capabilities: DISPLAY })
    expect(plain.$<HTMLButtonElement>('[data-smv="display"]').hidden).toBe(true)
    document.body.innerHTML = ''
    const bare = await live({ transport: display() }, { ...HELLO, capabilities: VIEW_ONLY })
    expect(bare.$<HTMLButtonElement>('[data-smv="display"]').hidden).toBe(true)
    document.body.innerHTML = ''
    const simulator = await opened(display(), DEVICE)
    expect(simulator.toggle.hidden).toBe(false)
    expect(simulator.row('motion')).toBeNull()
    expect([...simulator.panel.querySelectorAll('[data-smv-display]')].map((row) => row.textContent))
      .toEqual(['Demo status bar', 'Larger text', 'Smaller text', 'Increase contrast'])
    expect(simulator.panel.textContent).toContain('Text: large')
    simulator.toggle.click()
    expect(simulator.panel.hidden).toBe(true)
  })

  it('changes the device as sim_device would, follows what it did, and says so', async () => {
    const { calls, row, panel, $ } = await opened(display('text size extra-large'))
    row('status')!.click()
    await flush()
    expect(calls.changeDevice).toHaveBeenLastCalledWith({ action: 'status_bar', preset: 'demo' })
    expect(row('status')!.getAttribute('aria-checked')).toBe('true')
    row('status')!.click()
    await flush()
    expect(calls.changeDevice).toHaveBeenLastCalledWith({ action: 'status_bar', preset: 'clear' })
    for (let step = 0; step < TEXT_SIZES.length; step++) {
      row('larger')?.click()
      await flush()
    }
    expect(row('larger')).toBeNull()
    expect(panel.textContent).toContain('Text: extra extra extra large')
    for (let step = 0; step < TEXT_SIZES.length; step++) {
      row('smaller')?.click()
      await flush()
    }
    expect(row('smaller')).toBeNull()
    expect(calls.changeDevice).toHaveBeenLastCalledWith({ action: 'text_size', size: 'extra-small' })
    row('contrast')!.click()
    await flush()
    expect(calls.changeDevice).toHaveBeenLastCalledWith({ action: 'contrast', on: true })
    row('motion')!.click()
    await flush()
    expect(calls.changeDevice).toHaveBeenLastCalledWith({ action: 'reduce_motion', on: true })
    expect($<HTMLElement>('[data-smv-note]').textContent).toContain('text size extra-large')
  })

  it('says why a change was refused, and changes nothing it did not make', async () => {
    const calls = display()
    calls.changeDevice.mockRejectedValueOnce(new Error('the device is locked')).mockRejectedValueOnce(new Error(''))
    const { row, $ } = await opened(calls)
    row('contrast')!.click()
    await flush()
    expect($<HTMLElement>('[data-smv-note]').textContent).toContain('the device is locked')
    expect(row('contrast')!.getAttribute('aria-checked')).toBe('false')
    row('contrast')!.click()
    await flush()
    expect($<HTMLElement>('[data-smv-note]').textContent).toContain('The device did not change.')
  })

  it('finishes a change after the menu closed, and is closed when the device can make none', async () => {
    let finish: (said: string) => void = () => undefined
    const calls = { changeDevice: vi.fn(() => new Promise<string>((resolve) => { finish = resolve })) }
    const { row, toggle, panel } = await opened(calls)
    row('status')!.click()
    toggle.click()
    finish('demo status bar')
    await flush()
    expect(panel.hidden).toBe(true)
    toggle.click()
    socket().message({ ...HELLO, capabilities: VIEW_ONLY })
    expect(toggle.hidden).toBe(true)
    expect(panel.hidden).toBe(true)
    panel.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
})

describe('the Display menu, on its own', () => {
  function menu() {
    const root = document.createElement('div')
    const toggle = document.createElement('button')
    const panel = document.createElement('div')
    const noteEl = document.createElement('div')
    root.append(toggle, panel, noteEl)
    document.body.append(root)
    const changeDevice = vi.fn(async () => 'done')
    const made = createDisplayMenu({
      root, toggle, panel, layers: createLayers(), note: createNote(noteEl, lucideSvg), changeDevice,
    })
    return { made, toggle, panel, changeDevice }
  }

  it('repaints when what the device can do changes while it is open, and ignores what is not a row', async () => {
    const { made, toggle, panel, changeDevice } = menu()
    made.offer(['status_bar'], false)
    toggle.click()
    expect(panel.querySelectorAll('[data-smv-display]')).toHaveLength(1)
    const stale = panel.querySelector<HTMLButtonElement>('[data-smv-display="status"]')!
    made.offer(['accessibility'], true)
    expect([...panel.querySelectorAll('[data-smv-display]')].map((row) => row.getAttribute('data-smv-display')))
      .toEqual(['larger', 'smaller', 'contrast', 'motion'])
    panel.append(stale)
    stale.click()
    panel.querySelector<HTMLElement>('.smv-picker-note')!.click()
    await flush()
    expect(changeDevice).not.toHaveBeenCalled()
    made.destroy()
  })
})

describe('the note', () => {
  it('shows text as text, closes, and hides when asked', () => {
    const el = document.createElement('div')
    const note = createNote(el, lucideSvg)
    note.sayText('<b>not bold</b>')
    expect(el.hidden).toBe(false)
    expect(el.querySelector('b')).toBeNull()
    expect(el.getAttribute('role')).toBe('status')
    el.querySelector('p')!.click()
    expect(el.hidden).toBe(false)
    el.querySelector<HTMLButtonElement>('[data-smv-note-close]')!.click()
    expect(el.hidden).toBe(true)
    note.show('<p>again</p>')
    note.hide()
    expect(el.hidden).toBe(true)
    note.destroy()
  })
})

describe('an old server', () => {
  it('is read as a simulator with a usable picker row', async () => {
    const { $ } = setup({ transport: { devices: vi.fn(async () => [
      { udid: 'U9', name: 'Old', runtime: 'iOS 18', state: 'Booted', created: false },
    ]) } })
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    await flush()
    const row = $<HTMLButtonElement>('[data-smv-udid="U9"]')
    expect(row.getAttribute('aria-disabled')).toBeNull()
    expect(row.querySelector('span')!.textContent).toBe('iOS 18')
  })
})
