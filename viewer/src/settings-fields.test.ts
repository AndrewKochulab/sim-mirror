// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it, vi } from 'vitest'

import { entry } from '../test-support/settings'
import { renderField, type FieldOptions } from './settings-fields'

function options(overrides: Partial<FieldOptions> = {}): FieldOptions {
  return { editable: true, onInput: vi.fn(), onReset: vi.fn(), ...overrides }
}

const input = <T extends Element>(el: HTMLElement) => el.querySelector<T>('input, select, textarea')!
const chips = (el: HTMLElement) => [...el.querySelectorAll('.smv-chip')].map((chip) => chip.textContent)

function type(control: HTMLInputElement | HTMLTextAreaElement, text: string): void {
  control.value = text
  control.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('renderField', () => {
  it('labels its control with its path, says what it does, and tells when it changed', () => {
    const told = options()
    const field = renderField(entry('agent.cursor'), told)
    const control = input<HTMLInputElement>(field.el)
    const label = field.el.querySelector('label')!
    expect(label.textContent).toBe('agent.cursor')
    expect(label.htmlFor).toBe(control.id)
    expect(control.getAttribute('aria-describedby')).toBe(field.el.querySelector('.smv-field-doc')!.id)
    expect([control.type, control.getAttribute('role'), control.checked]).toEqual(['checkbox', 'switch', true])
    expect(field.dirty()).toBe(false)
    control.checked = false
    control.dispatchEvent(new Event('change', { bubbles: true }))
    expect([field.value(), field.dirty(), (told.onInput as ReturnType<typeof vi.fn>).mock.calls.length]).toEqual([false, true, 1])
    expect(field.el.dataset.smvSetting).toBe('agent.cursor')
    expect(renderField(entry('agent.cursor'), told).el.querySelector('input')!.id).not.toBe(control.id)
  })

  it('offers a whole number within its bounds, and sends what is not one as the text it is', () => {
    const field = renderField(entry('stream.fps', { rule: { kind: 'whole', low: 5, high: 60 }, value: 30 }), options())
    const control = input<HTMLInputElement>(field.el)
    expect([control.type, control.min, control.max, control.value]).toEqual(['number', '5', '60', '30'])
    type(control, '12')
    expect([field.value(), field.dirty()]).toEqual([12, true])
    Object.defineProperty(control, 'value', { configurable: true, get: () => 'fast' })
    expect(field.value()).toBe('fast')
  })

  it('offers a choice of its options, one line of text, and origins one per line', () => {
    const choice = renderField(entry('stream.encoding', { rule: { kind: 'choice', options: ['auto', 'h264'] },
      value: 'h264' }), options())  // prettier-ignore
    const select = input<HTMLSelectElement>(choice.el)
    expect([...select.options].map((option) => option.value)).toEqual(['auto', 'h264'])
    expect(choice.value()).toBe('h264')
    const text = renderField(entry('device.type', { rule: { kind: 'text', format: 'name', max_length: 100,
      required: false, example: 'iPhone 17 Pro', suggestions: null }, value: '' }), options())  // prettier-ignore
    const line = input<HTMLInputElement>(text.el)
    expect([line.type, line.maxLength, line.placeholder]).toEqual(['text', 100, 'iPhone 17 Pro'])
    expect([line.getAttribute('list'), text.el.querySelector('datalist')]).toEqual([null, null])
    type(line, '  iPhone Air ')
    expect(text.value()).toBe('iPhone Air')
    const origins = renderField(entry('security.allowed_origins', { rule: { kind: 'origins', max_items: 20 },
      value: ['http://localhost:3000'] }), options())  // prettier-ignore
    const area = input<HTMLTextAreaElement>(origins.el)
    expect(area.value).toBe('http://localhost:3000')
    type(area, 'http://a.test\n\n http://b.test, http://c.test ')
    expect(origins.value()).toEqual(['http://a.test', 'http://b.test', 'http://c.test'])
    const odd = renderField(entry('security.frame_ancestors', { rule: { kind: 'origins', max_items: 20 },
      value: 'not a list' }), options())  // prettier-ignore
    expect(input<HTMLTextAreaElement>(odd.el).value).toBe('')
  })

  it('offers the values a text setting suggests while any other may still be typed', () => {
    const rule = { kind: 'text' as const, format: 'connector', max_length: 32, required: true, example: 'auto' }
    const connector = renderField(entry('connectors.preferred', { rule: { ...rule, suggestions: ['auto', 'native', 'idb'] },
      value: 'auto' }), options())  // prettier-ignore
    const line = input<HTMLInputElement>(connector.el)
    const list = connector.el.querySelector('datalist')!
    expect(line.getAttribute('list')).toBe(list.id)
    expect(list.id).toBe(`${line.id}-choices`)
    expect([...list.options].map((option) => option.value)).toEqual(['auto', 'native', 'idb'])
    type(line, 'my-android')
    expect(connector.value()).toBe('my-android')
    const empty = renderField(entry('connectors.preferred', { rule: { ...rule, suggestions: [] } }), options())
    expect(empty.el.querySelector('datalist')).toBeNull()
  })

  it('says where its value comes from, when a change takes effect, whom it is for and when it needs the terminal', () => {
    expect(chips(renderField(entry('enabled'), options()).el)).toEqual(['from default'])
    const port = renderField(entry('server.port', { reach: 'global', effect: 'restart', sensitive: true,
      origin: { layer: 'file', detail: 'config.toml' } }), options())  // prettier-ignore
    expect(chips(port.el)).toEqual(['from config.toml', 'applies after a restart', 'whole daemon', 'confirm at the terminal'])
    const scoped = renderField(entry('stream.encoding', { effect: 'next_connection',
      origin: { layer: 'scope', detail: '[scopes."demo"]' } }), options())  // prettier-ignore
    expect(chips(scoped.el)).toEqual(['from this project', 'applies on the next connection'])
    const device = renderField(entry('device.type', { effect: 'next_device' }), options())
    expect(chips(device.el)).toEqual(['from default', 'applies on the next device'])
    const commandLine = renderField(entry('server.host', { origin: { layer: 'command_line', detail: null },
      locked: 'Set by the command line.' }), options())  // prettier-ignore
    expect(chips(commandLine.el)).toEqual(['from command line'])
    const variable = renderField(entry('stream.fps', { origin: { layer: 'environment', detail: 'SIM_MIRROR_STREAM_FPS' },
      locked: 'Set by SIM_MIRROR_STREAM_FPS.' }), options())  // prettier-ignore
    expect(chips(variable.el)).toEqual(['from SIM_MIRROR_STREAM_FPS'])
  })

  it('puts back a value config.toml holds, never a default or a locked one', () => {
    const onReset = vi.fn()
    const held = renderField(entry('stream.fps', { origin: { layer: 'file', detail: 'config.toml' } }), options({ onReset }))
    const reset = held.el.querySelector<HTMLButtonElement>('.smv-link')!
    expect(reset.textContent).toBe('Reset')
    reset.click()
    expect(onReset).toHaveBeenCalledWith('stream.fps')
    expect(renderField(entry('enabled'), options()).el.querySelector('.smv-link')).toBeNull()
    const readOnly = renderField(entry('stream.fps', { origin: { layer: 'file', detail: 'config.toml' } }),
      options({ editable: false }))  // prettier-ignore
    expect(readOnly.el.querySelector('.smv-link')).toBeNull()
    expect(input<HTMLInputElement>(readOnly.el).disabled).toBe(true)
  })

  it('cannot be changed while something above config.toml sets it, and says so without a command that could not', () => {
    const locked = renderField(entry('stream.fps', { origin: { layer: 'environment', detail: 'SIM_MIRROR_STREAM_FPS' },
      locked: 'Set by SIM_MIRROR_STREAM_FPS in the environment.', command: 'sim-mirror config set stream.fps <value>' }),
      options())  // prettier-ignore
    expect(input<HTMLInputElement>(locked.el).disabled).toBe(true)
    expect(locked.el.querySelector('.smv-field-locked')!.textContent).toBe('Set by SIM_MIRROR_STREAM_FPS in the environment.')
    expect(locked.el.querySelector('code')).toBeNull()
    expect(locked.el.querySelector('.smv-link')).toBeNull()
  })

  it('shows the server’s problem with it until it is cleared, never as markup', () => {
    const field = renderField(entry('stream.fps'), options())
    const error = field.el.querySelector<HTMLElement>('.smv-field-error')!
    expect(error.hidden).toBe(true)
    field.setError('<b>stream.fps</b> must be a whole number between 5 and 60')
    expect([error.hidden, error.textContent, error.querySelector('b')]).toEqual([
      false, '<b>stream.fps</b> must be a whole number between 5 and 60', null])
    expect(field.el.classList.contains('smv-field-invalid')).toBe(true)
    expect(input<HTMLInputElement>(field.el).getAttribute('aria-invalid')).toBe('true')
    field.setError(null)
    expect([error.hidden, error.textContent, field.el.classList.contains('smv-field-invalid')]).toEqual([true, '', false])
  })
})
