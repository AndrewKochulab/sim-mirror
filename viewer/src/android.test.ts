// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FakeSocket } from '../test-support/fake-socket'
import { settingsView } from '../test-support/settings'
import { DEVICE, FULL, HELLO, flush, live, said, socket } from '../test-support/viewer-harness'
import { DEFAULT_BUTTONS, buttonsOf, connectionLabel, deviceNoun, isReal, kindChip } from './device-kind'
import { lucideSvg } from './icons'
import type { Capability, Device, DeviceChoice, TouchSetup } from './protocol.generated'

const EMULATOR: Device = {
  ...DEVICE, udid: 'avd-Pixel-9-fa39dd', name: 'Pixel 9', runtime: 'Android 16 (API 36)', kind: 'android',
  connection: null, created: false, booted_by_us: true, buttons: ['back', 'home', 'app_switch', 'lock', 'volume_up',
    'volume_down'],
}
const PHONE: Device = {
  ...EMULATOR, udid: 'adb-R58M12ABC-9b1e04', name: 'Pixel 8', runtime: 'Android 15 (API 35)', connection: 'usb',
  booted_by_us: false,
}
/** What adb alone gives: everything but a live stream. */
const ADB_ONLY: Capability[] = FULL.filter((capability) => capability !== 'stream_h264')
const BAR = ['back', 'home', 'app_switch', 'lock', 'volume_down', 'volume_up']

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 1080, height: 2424, close: vi.fn() })))
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as never
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('what a device is', () => {
  it('is a simulator, an emulator or a real device, with how it is connected', () => {
    expect([DEVICE, EMULATOR, PHONE, { ...PHONE, kind: 'physical' as const }].map(deviceNoun))
      .toEqual(['simulator', 'emulator', 'device', 'device'])
    expect([null, DEVICE, EMULATOR, PHONE].map(isReal)).toEqual([false, false, false, true])
    expect(deviceNoun(null)).toBe('simulator')
    expect([DEVICE, EMULATOR, PHONE, { ...PHONE, connection: 'network' as const }].map(kindChip))
      .toEqual(['', 'Android · Emulator', 'Android · USB', 'Android · Wi-Fi'])
    expect([connectionLabel('usb'), connectionLabel('network'), connectionLabel(null)])
      .toEqual(['USB', 'Wi-Fi', 'Not connected'])
    // An older server sends no buttons: Home and Lock, as the bar always had.
    expect(buttonsOf({ ...DEVICE, buttons: undefined } as unknown as Device)).toBe(DEFAULT_BUTTONS)
    expect(buttonsOf(null)).toBe(DEFAULT_BUTTONS)
  })

  it('draws the Android buttons with icons of their own', () => {
    for (const name of ['back', 'app_switch', 'volume_up', 'volume_down'] as const) {
      expect(lucideSvg(name)).toContain(`data-icon="${name}"`)
    }
  })
})

describe('an Android device in the bar', () => {
  const visible = (find: <T extends Element>(selector: string) => T) =>
    BAR.filter((name) => !find<HTMLButtonElement>(`[data-smv="${name}"]`).hidden)

  it('offers the buttons the device has, and presses each', async () => {
    const { $ } = await live()
    expect(visible($)).toEqual(['home', 'lock'])
    socket().message({ type: 'status', ...EMULATOR })
    expect(visible($)).toEqual(BAR)
    socket().sent = []
    for (const name of BAR) $<HTMLButtonElement>(`[data-smv="${name}"]`).click()
    expect(socket().sent).toEqual(BAR.map((name) => ({ type: 'button', name })))
    expect($<HTMLButtonElement>('[data-smv="back"]').title).toBe('Back')
    socket().message({ type: 'status', ...EMULATOR, buttons: [] })
    expect(visible($)).toEqual([])
    socket().message({ type: 'status', ...DEVICE })
    expect(visible($)).toEqual(['home', 'lock'])
  })

  it('offers none while its connector presses nothing', async () => {
    const { $ } = await live({}, { ...HELLO, capabilities: FULL.filter((capability) => capability !== 'input_button') })
    socket().message({ type: 'status', ...EMULATOR })
    expect(visible($)).toEqual([])
  })

  it('is named an Android emulator or phone, started or shown, and let go as it is', async () => {
    const { $, view, transport } = await live()
    const kind = $<HTMLElement>('[data-smv-kind]')
    socket().message({ type: 'status', ...EMULATOR, state: 'stopped' })
    expect([kind.hidden, kind.textContent]).toEqual([false, 'Android · Emulator'])
    expect(said(view)).toContain('The emulator is stopped.')
    expect($<HTMLButtonElement>('[data-smv-empty] [data-smv="start"]').textContent).toBe('Start the emulator')
    expect($<HTMLButtonElement>('[data-smv="stop"]').title).toBe('Let the emulator go')
    socket().message({ type: 'status', ...EMULATOR })
    $<HTMLButtonElement>('[data-smv="stop"]').click()
    expect(said(view)).toContain('The emulator is let go; it keeps running for next time.')
    expect(transport.stop).toHaveBeenLastCalledWith(false)
    document.body.innerHTML = ''
    const phone = await live()
    socket().message({ type: 'status', ...PHONE, state: 'stopped' })
    expect(phone.$<HTMLElement>('[data-smv-kind]').textContent).toBe('Android · USB')
    expect(phone.$<HTMLButtonElement>('[data-smv-empty] [data-smv="start"]').textContent).toBe('Show the device')
    expect(phone.$<HTMLButtonElement>('[data-smv="stop"]').title).toBe('Let the device go')
  })

  it('says the emulator, not the simulator, when its screen closes', async () => {
    const { view } = await live()
    socket().message({ type: 'status', ...EMULATOR })
    socket().end(1014, 'it went away')
    expect(said(view)).toContain("The emulator's screen closed: it went away. Reconnecting…")
  })
})

describe('an Android device without scrcpy', () => {
  const OFFER: TouchSetup = { state: 'offer', message: 'scrcpy shows it live', team: null, team_from: null, last_error: null }

  it('offers Set up touch, which opens the setup; with scrcpy the badge is gone', async () => {
    const touch = vi.fn(async () => OFFER)
    const { $ } = await live({ transport: { touch, setUpTouch: vi.fn() } }, { ...HELLO, capabilities: ADB_ONLY })
    const mode = $<HTMLButtonElement>('[data-smv="mode"]')
    socket().message({ type: 'status', ...EMULATOR })
    expect([mode.hidden, mode.textContent]).toEqual([false, 'Set up touch'])
    expect(mode.title).toContain('Without scrcpy')
    mode.click()
    await flush()
    expect(touch).toHaveBeenCalledOnce()
    expect($<HTMLElement>('[data-smv-note]').textContent).toContain('scrcpy shows it live')
    document.body.innerHTML = ''
    const live264 = await live({ transport: { touch, setUpTouch: vi.fn() } })
    socket().message({ type: 'status', ...EMULATOR })
    expect(live264.$<HTMLButtonElement>('[data-smv="mode"]').hidden).toBe(true)
  })

  it("opens the Android settings from the setup's note, and from the badge where no setup is offered", async () => {
    const touch = vi.fn(async (): Promise<TouchSetup> => ({ ...OFFER, state: 'off', message: 'scrcpy is off' }))
    const settings = vi.fn(async () => settingsView({
      sections: [{ id: '', title: 'General', doc: '' }, { id: 'android', title: 'Android', doc: '' }],
    }))
    const { $ } = await live(
      { transport: { touch, setUpTouch: vi.fn(), settings, changeSettings: vi.fn() } }, { ...HELLO, capabilities: ADB_ONLY },
    )
    socket().message({ type: 'status', ...EMULATOR })
    $<HTMLButtonElement>('[data-smv="mode"]').click()
    await flush()
    $<HTMLButtonElement>('[data-smv-touch="settings"]').click()
    await flush()
    expect($<HTMLElement>('[data-smv-settings] [aria-selected="true"]').textContent).toBe('Android')
    document.body.innerHTML = ''
    const bare = await live({ transport: { settings, changeSettings: vi.fn() } }, { ...HELLO, capabilities: ADB_ONLY })
    socket().message({ type: 'status', ...EMULATOR })
    bare.$<HTMLButtonElement>('[data-smv="mode"]').click()
    await flush()
    expect(bare.$<HTMLElement>('[data-smv-settings] [aria-selected="true"]').textContent).toBe('Android')
  })
})

describe('the picker with Android devices', () => {
  const choice = (device: Device, extra: Partial<DeviceChoice> = {}): DeviceChoice => ({
    udid: device.udid, name: device.name, runtime: device.runtime, state: 'Booted', created: false, kind: device.kind,
    connection: device.connection, detail: null, usable: true, ...extra,
  })
  const choices = [
    choice(DEVICE), choice(EMULATOR), choice(PHONE, { state: 'Connected' }),
    choice({ ...PHONE, udid: 'adb-R58OFF-000000', name: 'Locked phone' }, { detail: 'Allow USB debugging', usable: false }),
  ]

  it('groups them after the simulators, says how each is reached, and will not pick one still to be allowed', async () => {
    const { $, transport } = await live({ transport: { devices: vi.fn(async () => choices) } })
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    await flush()
    const picker = $<HTMLElement>('[data-smv-picker]')
    expect([...picker.querySelectorAll('.smv-picker-group')].map((group) => group.textContent))
      .toEqual(['Simulators', 'Android'])
    const rows = [...picker.querySelectorAll<HTMLButtonElement>('[data-smv-udid]')]
    expect(rows.map((row) => row.querySelector('span')!.textContent)).toEqual([
      'iOS 26.5', 'Android 16 (API 36) · Emulator', 'Android 15 (API 35) · USB',
      'Android 15 (API 35) · USB · Allow USB debugging',
    ])
    expect(picker.querySelector('[data-smv="shutdown"]')).not.toBeNull()
    rows[3].click()
    await flush()
    expect(transport.choose).not.toHaveBeenCalled()
  })

  it('never offers to shut down an Android phone, only an emulator', async () => {
    const { $ } = await live({ transport: { devices: vi.fn(async () => choices) } })
    socket().message({ type: 'status', ...PHONE })
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    await flush()
    expect($<HTMLElement>('[data-smv-picker]').querySelector('[data-smv="shutdown"]')).toBeNull()
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    socket().message({ type: 'status', ...EMULATOR })
    $<HTMLButtonElement>('[data-smv="devices"]').click()
    await flush()
    expect($<HTMLElement>('[data-smv-picker]').querySelector('[data-smv="shutdown"]')).not.toBeNull()
  })
})

describe('the Display menu on an Android device', () => {
  it('offers reduce motion, as on any device but a simulator', async () => {
    const { $ } = await live({ transport: { changeDevice: vi.fn(async () => 'done') } })
    socket().message({ type: 'status', ...EMULATOR })
    $<HTMLButtonElement>('[data-smv="display"]').click()
    expect($<HTMLElement>('[data-smv-display]').querySelector('[data-smv-display="motion"]')).not.toBeNull()
  })
})
