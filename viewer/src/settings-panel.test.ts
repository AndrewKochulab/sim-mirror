// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from 'vitest'

import { settingsView } from '../test-support/settings'
import { TransportError } from './http-transport'
import { lucideSvg } from './icons'
import { createLayers } from './popover'
import type { SettingsChange, SettingsView } from './protocol.generated'
import { createSettingsPanel, type SettingsTransport } from './settings-panel'

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function setup(transport: Partial<SettingsTransport> = {}) {
  const root = document.createElement('div')
  root.innerHTML = '<button type="button" data-toggle aria-expanded="false">Settings</button><div data-panel hidden></div>'
  document.body.appendChild(root)
  const toggle = root.querySelector<HTMLElement>('[data-toggle]')!
  const panel = root.querySelector<HTMLElement>('[data-panel]')!
  const calls = {
    settings: vi.fn(async (): Promise<SettingsView> => settingsView()),
    changeSettings: vi.fn(async (change: SettingsChange): Promise<SettingsView> => {
      const next = settingsView()
      for (const { path, value } of change.set) next.settings.find((setting) => setting.path === path)!.value = value
      return next
    }),
    ...transport,
  }
  const layers = createLayers()
  const settings = createSettingsPanel({ root, toggle, panel, transport: calls as SettingsTransport, layers, icon: lucideSvg })
  const $ = <T extends Element>(selector: string) => panel.querySelector<T>(selector)!
  const $$ = <T extends Element>(selector: string) => [...panel.querySelectorAll<T>(selector)]
  const tabs = () => $$<HTMLButtonElement>('[role="tab"]')
  const selected = () => tabs().find((tab) => tab.getAttribute('aria-selected') === 'true')!.textContent
  const field = (path: string) => $<HTMLElement>(`[data-smv-setting="${path}"]`)
  const control = <T extends Element>(path: string) => field(path).querySelector<T>('input, select, textarea')!
  const status = () => $<HTMLElement>('.smv-settings-status').textContent
  const save = () => $<HTMLButtonElement>('.smv-button-primary')
  return { root, toggle, panel, calls, layers, settings, $, $$, tabs, selected, field, control, status, save }
}

async function opened(transport: Partial<SettingsTransport> = {}) {
  const rig = setup(transport)
  await rig.settings.toggle()
  await flush()
  return rig
}

function edit(control: HTMLInputElement | HTMLSelectElement, value: string): void {
  control.value = value
  control.dispatchEvent(new Event('input', { bubbles: true }))
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('the settings panel', () => {
  it('opens over the screen with a tab per section, the first chosen and focused, holding input back', async () => {
    const { settings, panel, toggle, layers, tabs, selected, $, $$ } = await opened()
    expect([settings.isOpen, panel.hidden, toggle.getAttribute('aria-expanded'), layers.open]).toEqual([true, false, 'true', true])
    expect(tabs().map((tab) => tab.textContent)).toEqual(['General', 'Stream', 'Server'])
    expect(selected()).toBe('General')
    expect(document.activeElement).toBe(tabs()[0])
    expect($('.smv-settings-scope').textContent).toBe('demo')
    expect(panel.getAttribute('aria-labelledby')).toBe($('.smv-settings-title').id)
    const panes = $$<HTMLElement>('[role="tabpanel"]')
    expect(panes.map((pane) => pane.hidden)).toEqual([false, true, true])
    expect(tabs()[1].getAttribute('aria-controls')).toBe(panes[1].id)
    expect(panes[1].querySelectorAll('.smv-field')).toHaveLength(3)
    expect($('.smv-settings-notice')).toBeNull()
  })

  it('moves between tabs by clicking and with the arrows, Home and End', async () => {
    const { tabs, selected, $ } = await opened()
    tabs()[1].click()
    expect(selected()).toBe('Stream')
    const key = (name: string) => {
      const event = new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true })
      $('[role="tablist"]').dispatchEvent(event)
      return event
    }
    expect(key('ArrowRight').defaultPrevented).toBe(true)
    expect([selected(), document.activeElement]).toEqual(['Server', tabs()[2]])
    key('ArrowRight')
    expect(selected()).toBe('General')
    key('ArrowLeft')
    expect(selected()).toBe('Server')
    key('Home')
    expect(selected()).toBe('General')
    key('End')
    expect(selected()).toBe('Server')
    expect(key('a').defaultPrevented).toBe(false)
    expect(tabs().map((tab) => tab.tabIndex)).toEqual([-1, -1, 0])
  })

  it('saves a tab’s changed values for this project, and shows them as they now are', async () => {
    const { calls, tabs, control, save, status, selected } = await opened()
    tabs()[1].click()
    expect(save().disabled).toBe(true)
    edit(control<HTMLInputElement>('stream.fps'), '12')
    expect(save().disabled).toBe(false)
    save().click()
    await flush()
    expect(calls.changeSettings).toHaveBeenCalledWith({
      target: 'scope', set: [{ path: 'stream.fps', value: 12 }], unset: [], confirmation: null,
    })
    expect([control<HTMLInputElement>('stream.fps').value, status(), selected(), save().disabled]).toEqual([
      '12', 'Saved.', 'Stream', true])
  })

  it('saves for every project when asked, and a tab only the whole daemon has always is', async () => {
    const { calls, tabs, control, save, $, $$ } = await opened()
    tabs()[1].click()
    const [, every] = $$<HTMLInputElement>('.smv-targets input')
    every.checked = true
    every.dispatchEvent(new Event('change'))
    edit(control<HTMLSelectElement>('stream.encoding'), 'jpeg')
    save().click()
    await flush()
    expect(vi.mocked(calls.changeSettings).mock.calls[0][0].target).toBe('all')
    tabs()[2].click()
    expect($<HTMLElement>('.smv-targets').hidden).toBe(true)
    edit(control<HTMLInputElement>('server.port'), '7481')
    save().click()
    await flush()
    expect(vi.mocked(calls.changeSettings).mock.calls[1][0]).toEqual({
      target: 'all', set: [{ path: 'server.port', value: 7481 }], unset: [], confirmation: null,
    })
  })

  it('puts a value back where it came from: this project’s table, or the whole file', async () => {
    const { calls, tabs, field, settings, root } = await opened()
    tabs()[1].click()
    const reset = field('stream.fps').querySelector<HTMLButtonElement>('.smv-link')!
    reset.focus()
    reset.click()
    await flush()
    // The button that had focus was drawn again: focus stays in the panel, on its tab, so Escape still closes it.
    expect(document.activeElement).toBe(tabs()[1])
    document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    expect(settings.isOpen).toBe(false)
    expect(root.contains(document.activeElement)).toBe(true)
    await settings.toggle()
    await flush()
    tabs()[1].click()
    field('stream.encoding').querySelector<HTMLButtonElement>('.smv-link')!.click()
    await flush()
    expect(vi.mocked(calls.changeSettings).mock.calls.map(([change]) => [change.target, change.unset])).toEqual([
      ['scope', ['stream.fps']], ['all', ['stream.encoding']],
    ])
  })

  it('shows each problem on the setting it names, and a plain failure as what it said', async () => {
    const refusal = {
      detail: 'stream.fps must be a whole number between 5 and 60', confirmation: null,
      errors: [{ path: 'stream.fps', message: 'stream.fps must be a whole number between 5 and 60' },
        { path: 'colour', message: 'colour is not a setting' }],
    }
    const changeSettings = vi.fn()
      .mockRejectedValueOnce(new TransportError(refusal.detail, 422, refusal))
      .mockRejectedValueOnce(new Error('network down'))
      .mockRejectedValueOnce(new Error(''))
    const { tabs, control, field, save, status } = await opened({ changeSettings })
    tabs()[1].click()
    edit(control<HTMLInputElement>('stream.fps'), '999')
    save().click()
    await flush()
    expect(field('stream.fps').querySelector('.smv-field-error')!.textContent).toBe(refusal.errors[0].message)
    expect([status(), save().disabled]).toEqual([refusal.detail, false])
    save().click()
    await flush()
    expect(field('stream.fps').querySelector<HTMLElement>('.smv-field-error')!.hidden).toBe(true)
    expect(status()).toBe('network down')
    save().click()
    await flush()
    expect(status()).toBe('The settings could not be saved.')
  })

  it('asks for the terminal’s code for a sensitive change and sends the same change again with it', async () => {
    const refusal = {
      detail: 'Changing server.port needs a person at the terminal: run `sim-mirror settings confirm`.', errors: [],
      confirmation: { id: 'c1', summary: 'demo: server.port = 7481', command: 'sim-mirror settings confirm', expires_in_s: 300 },
    }
    const changeSettings = vi.fn()
      .mockRejectedValueOnce(new TransportError(refusal.detail, 428, refusal))
      .mockRejectedValueOnce(new TransportError(refusal.detail, 428, refusal))
      .mockResolvedValueOnce(settingsView())
    const { tabs, control, save, status, $ } = await opened({ changeSettings })
    tabs()[2].click()
    edit(control<HTMLInputElement>('server.port'), '7481')
    save().click()
    await flush()
    const confirm = $<HTMLElement>('.smv-confirm')
    const code = confirm.querySelector<HTMLInputElement>('input')!
    expect([confirm.hidden, $('.smv-confirm-text').textContent, document.activeElement]).toEqual([false, refusal.detail, code])
    expect(status()).toBe('Waiting for the code for: demo: server.port = 7481')
    code.value = ' wrong '
    confirm.querySelector<HTMLButtonElement>('button')!.click()
    await flush()
    expect(changeSettings.mock.calls[1][0].confirmation).toBe('wrong')
    $<HTMLElement>('.smv-confirm').querySelector<HTMLInputElement>('input')!.value = 'ABCD-EFGH'
    $<HTMLElement>('.smv-confirm').querySelector<HTMLButtonElement>('button')!.click()
    await flush()
    expect(changeSettings.mock.calls[2][0]).toEqual({
      target: 'all', set: [{ path: 'server.port', value: 7481 }], unset: [], confirmation: 'ABCD-EFGH',
    })
    expect([$<HTMLElement>('.smv-confirm').hidden, status()]).toEqual([true, 'Saved.'])
  })

  it('lets a page that may only read see everything and change nothing, and says how to change them', async () => {
    const view = settingsView({ access: 'read', notice: 'This page can read the project’s settings but not change them.' })
    const { $, $$, save, tabs } = await opened({ settings: vi.fn(async () => view) })
    expect($('.smv-settings-notice').textContent).toBe(view.notice)
    tabs()[1].click()
    expect($$<HTMLInputElement>('.smv-field input, .smv-field select').every((control) => control.disabled)).toBe(true)
    expect([save().disabled, $<HTMLElement>('.smv-targets').hidden, $$('.smv-link')]).toEqual([true, true, []])
  })

  it('says when the settings cannot be read, and draws nothing into a panel closed while they were', async () => {
    const failing = setup({ settings: vi.fn().mockRejectedValueOnce(new Error('the daemon is gone')).mockRejectedValueOnce({}) })
    await failing.settings.toggle()
    expect(failing.status()).toBe('the daemon is gone')
    failing.settings.close()
    await failing.settings.toggle()
    expect(failing.status()).toBe('The settings could not be read.')
    const slow = deferred<SettingsView>()
    const closing = setup({ settings: vi.fn(() => slow.promise) })
    const loading = closing.settings.toggle()
    await closing.settings.toggle()
    slow.resolve(settingsView())
    await loading
    expect([closing.settings.isOpen, closing.panel.children.length]).toEqual([false, 0])
    const late = deferred<SettingsView>()
    const refused = setup({ settings: vi.fn(() => late.promise) })
    const reading = refused.settings.toggle()
    refused.settings.close()
    late.reject(new Error('too late'))
    await reading
    expect(refused.panel.children.length).toBe(0)
  })

  it('shows nothing from a save that finishes after it was closed', async () => {
    const saved = deferred<SettingsView>()
    const failed = deferred<SettingsView>()
    const changeSettings = vi.fn().mockReturnValueOnce(saved.promise).mockReturnValueOnce(failed.promise)
    const rig = await opened({ changeSettings })
    rig.tabs()[1].click()
    edit(rig.control<HTMLInputElement>('stream.fps'), '12')
    rig.save().click()
    rig.settings.close()
    saved.resolve(settingsView())
    await flush()
    expect(rig.panel.children.length).toBe(0)
    await rig.settings.toggle()
    await flush()
    rig.tabs()[1].click()
    edit(rig.control<HTMLInputElement>('stream.fps'), '13')
    rig.save().click()
    rig.settings.close()
    failed.reject(new Error('refused'))
    await flush()
    expect(rig.panel.children.length).toBe(0)
  })

  it('closes from its own button and on Escape, not on a press outside, and lets go when destroyed', async () => {
    const rig = await opened()
    document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    expect(rig.settings.isOpen).toBe(true)
    rig.$<HTMLButtonElement>('.smv-settings-head .smv-icon').click()
    expect([rig.settings.isOpen, rig.panel.children.length, rig.layers.open]).toEqual([false, 0, false])
    await rig.settings.toggle()
    await flush()
    rig.tabs()[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    expect([rig.settings.isOpen, document.activeElement]).toEqual([false, rig.toggle])
    await rig.settings.toggle()
    await flush()
    await rig.settings.toggle()
    expect(rig.settings.isOpen).toBe(false)
    rig.settings.destroy()
    expect(rig.layers.open).toBe(false)
  })

  it('draws a server with no sections as an empty panel rather than failing', async () => {
    const { tabs, $ } = await opened({ settings: vi.fn(async () => settingsView({ sections: [], settings: [] })) })
    expect([tabs(), $('.smv-settings-title').textContent]).toEqual([[], 'Settings'])
  })
})
