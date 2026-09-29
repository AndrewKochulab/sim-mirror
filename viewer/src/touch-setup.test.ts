// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FakeSocket } from '../test-support/fake-socket'
import { settingsView } from '../test-support/settings'
import { CLOCK, DEVICE, HELLO, VIEW_ONLY, flush, live, socket } from '../test-support/viewer-harness'
import { lucideSvg } from './icons'
import { createNote } from './note'
import type { Device, TouchSetup } from './protocol.generated'
import { createTouchSetup, touchMarkup } from './touch-setup'

const PHONE: Device = {
  ...DEVICE, udid: '00008120-0011223344556677', name: 'Test iPhone', runtime: 'iOS 26.3',
  kind: 'physical', connection: 'usb', created: false, booted_by_us: false,
}
const OFFER: TouchSetup = {
  state: 'offer', message: 'Touching and reading it needs WebDriverAgent, set up once.', team: 'TESTTEAM01',
  team_from: 'project',
}
const BUILDING: TouchSetup = { ...OFFER, state: 'building', message: 'WebDriverAgent is being built for team TESTTEAM01.' }
const READY: TouchSetup = { state: 'ready', message: '', team: null, team_from: null }

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

describe('how touch stands, as the note shows it', () => {
  const button = (markup: string) => {
    const el = document.createElement('div')
    el.innerHTML = markup
    return el.querySelector<HTMLButtonElement>('[data-smv-touch]')
  }

  it('offers what a person can do next, and says whose team signs it', () => {
    const offered = touchMarkup(OFFER, true)
    expect(button(offered)!.textContent).toBe('Set up touch')
    expect(offered).toContain("Signed by team TESTTEAM01: the project's own team.")
    expect(button(touchMarkup({ ...OFFER, state: 'failed', message: 'it failed' }, true))!.textContent).toBe('Try again')
    const unsigned: TouchSetup = { state: 'needs_team', message: 'no team', team: null, team_from: null }
    expect(button(touchMarkup(unsigned, true))!.dataset.smvTouch).toBe('settings')
    expect(button(touchMarkup(unsigned, false))).toBeNull()
    expect(button(touchMarkup({ ...unsigned, state: 'off' }, true))!.textContent).toBe('Open settings')
    expect(button(touchMarkup(BUILDING, true))).toBeNull()
    expect(touchMarkup({ ...OFFER, team_from: 'mac' }, true)).toContain('the only team on this Mac')
    expect(touchMarkup({ ...OFFER, team_from: 'setting' }, true)).toContain('real_devices.team_id')
  })

  it('says a device that can be touched can, one that needs nothing needs nothing, and escapes what it is told', () => {
    expect(touchMarkup(READY, true)).toContain('This device can be touched.')
    expect(touchMarkup({ ...READY, state: 'not_needed' }, true)).toContain('a simulator is touched as it is')
    expect(touchMarkup({ ...BUILDING, message: '' }, true)).toContain('Touch is being set up.')
    expect(touchMarkup({ ...OFFER, message: '<b>x</b>', team: '<i>' }, true)).not.toMatch(/<b>|<i>/)
  })
})

describe('setting touch up', () => {
  function made(touch: () => Promise<TouchSetup>, setUpTouch: () => Promise<TouchSetup>, settings = true) {
    const el = document.createElement('div')
    document.body.append(el)
    const note = createNote(el, lucideSvg)
    const openSettings = vi.fn()
    const transport = { touch: vi.fn(touch), setUpTouch: vi.fn(setUpTouch) }
    const control = createTouchSetup({
      noteEl: el, note, transport, openSettings: settings ? openSettings : undefined,
    })
    const press = (action: string) => el.querySelector<HTMLButtonElement>(`[data-smv-touch="${action}"]`)!.click()
    return { el, note, transport, control, openSettings, press }
  }

  it('builds it for the device and follows it until the device can be touched', async () => {
    vi.useFakeTimers(CLOCK)
    const answers = [BUILDING, { ...BUILDING, state: 'starting' as const, message: 'Starting it.' }, READY]
    const { el, transport, control, press } = made(async () => answers.shift()!, async () => BUILDING)
    transport.touch.mockResolvedValueOnce(OFFER)
    await control.open()
    expect(el.textContent).toContain('set up once')
    press('set-up')
    await flush()
    expect(transport.setUpTouch).toHaveBeenCalledOnce()
    expect(el.querySelector<HTMLElement>('[data-smv-touch-note]')!.dataset.state).toBe('building')
    for (const state of ['building', 'starting', 'ready']) {
      await vi.advanceTimersByTimeAsync(3000)
      expect(el.querySelector<HTMLElement>('[data-smv-touch-note]')!.dataset.state).toBe(state)
    }
    await vi.advanceTimersByTimeAsync(9000)
    expect(transport.touch).toHaveBeenCalledTimes(4)
    expect(el.textContent).toContain('This device can be touched.')
    control.destroy()
  })

  it('stops following once the note shows something else, or is closed', async () => {
    vi.useFakeTimers(CLOCK)
    const { note, transport, control } = made(async () => BUILDING, async () => BUILDING)
    await control.open()
    note.sayText('Recorded 0:07 of Test iPhone.')
    await vi.advanceTimersByTimeAsync(3000)
    expect(transport.touch).toHaveBeenCalledTimes(1)
    await control.open()
    note.hide()
    await vi.advanceTimersByTimeAsync(3000)
    expect(transport.touch).toHaveBeenCalledTimes(2)
    await control.open()
    control.destroy()
    await vi.advanceTimersByTimeAsync(3000)
    expect(transport.touch).toHaveBeenCalledTimes(3)
  })

  it('tries a failed setup again, sends a person to the settings for a team, and says what went wrong', async () => {
    const failed: TouchSetup = { ...OFFER, state: 'failed', message: 'WebDriverAgent could not be set up.' }
    const { el, transport, control, openSettings, press } = made(async () => failed, async () => READY)
    await control.open()
    press('set-up')
    await flush()
    expect(el.textContent).toContain('This device can be touched.')
    transport.touch.mockResolvedValueOnce({ state: 'needs_team', message: 'no team', team: null, team_from: null })
    await control.open()
    press('settings')
    expect(openSettings).toHaveBeenCalledOnce()
    expect(el.hidden).toBe(true)
    transport.touch.mockRejectedValueOnce(new Error('the daemon went away'))
    await control.open()
    expect(el.textContent).toContain('the daemon went away')
    transport.touch.mockRejectedValueOnce(new Error(''))
    await control.open()
    expect(el.textContent).toContain('Touch could not be set up.')
    el.querySelector('p')!.click()
    expect(transport.setUpTouch).toHaveBeenCalledOnce()
  })

  it('offers no settings button where there are no settings', async () => {
    const { el, control } = made(
      async () => ({ state: 'off', message: 'off here', team: null, team_from: null }), async () => READY, false,
    )
    await control.open()
    expect(el.querySelector('[data-smv-touch]')).toBeNull()
  })
})

describe('Set up touch in the viewer', () => {
  it("names a real device's view-only badge Set up touch and a simulator's View only", async () => {
    const { $ } = await live({}, { ...HELLO, connector: 'iphone', capabilities: VIEW_ONLY })
    const mode = $<HTMLButtonElement>('[data-smv="mode"]')
    expect(mode.textContent).toBe('View only')
    socket().message({ type: 'status', ...PHONE })
    expect(mode.textContent).toBe('Set up touch')
    socket().message({ type: 'status', ...DEVICE })
    expect(mode.textContent).toBe('View only')
  })

  it("opens the setup from a real device's badge, and its settings from the note's button", async () => {
    const touch = vi.fn(async (): Promise<TouchSetup> => ({ state: 'needs_team', message: 'no team', team: null,
      team_from: null }))
    const settings = vi.fn(async () => settingsView({
      sections: [{ id: '', title: 'General', doc: '' }, { id: 'real_devices', title: 'Real devices', doc: '' }],
    }))
    const { $ } = await live(
      { transport: { touch, setUpTouch: vi.fn(), settings, changeSettings: vi.fn() } },
      { ...HELLO, connector: 'iphone', capabilities: VIEW_ONLY },
    )
    socket().message({ type: 'status', ...PHONE })
    $<HTMLButtonElement>('[data-smv="mode"]').click()
    await flush()
    expect(touch).toHaveBeenCalledOnce()
    expect($<HTMLElement>('[data-smv-note]').textContent).toContain('no team')
    $<HTMLButtonElement>('[data-smv-touch="settings"]').click()
    await flush()
    expect($<HTMLElement>('[data-smv-settings] [aria-selected="true"]').textContent).toBe('Real devices')
  })

  it("opens a real device's setup even where there are no settings", async () => {
    const touch = vi.fn(async (): Promise<TouchSetup> => OFFER)
    const { $ } = await live({ transport: { touch, setUpTouch: vi.fn() } }, { ...HELLO, capabilities: VIEW_ONLY })
    socket().message({ type: 'status', ...PHONE })
    $<HTMLButtonElement>('[data-smv="mode"]').click()
    await flush()
    expect($<HTMLButtonElement>('[data-smv-touch="set-up"]').textContent).toBe('Set up touch')
  })
})
