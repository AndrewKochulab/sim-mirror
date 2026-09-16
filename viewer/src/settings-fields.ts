// SPDX-License-Identifier: Apache-2.0
/**
 * One setting as a form field: its name, what it does, a control for its rule's kind, where its value comes from and
 * when a change takes effect -- or, when something above config.toml sets it, why it cannot be changed here and the
 * command that changes it instead.
 *
 * A field is built from the server's `SettingEntry` alone, with elements and text rather than markup, so nothing a
 * setting says is ever read as HTML. Whether a value is allowed is the server's to say (`setError`): the controls only
 * keep a person within what the rule plainly allows, such as a number's bounds.
 */
import type { RuleSpec, SettingEntry, SettingValue } from './protocol.generated'

export interface Field {
  readonly el: HTMLElement
  readonly entry: SettingEntry
  /** The value the control holds now. */
  value(): SettingValue
  /** Whether it holds something other than the value it was built with. */
  dirty(): boolean
  /** Show the server's problem with this setting, or clear it. */
  setError(message: string | null): void
}

export interface FieldOptions {
  /** Whether the asker may change settings at all. */
  editable: boolean
  /** Told when the control's value changes. */
  onInput(): void
  /** Put the setting back to what the layer below says. */
  onReset(path: string): void
}

/** Where a value comes from, in a person's words. */
export const ORIGIN_LABELS: Readonly<Record<SettingEntry['origin']['layer'], string>> = {
  default: 'default',
  file: 'config.toml',
  scope: 'this project',
  environment: 'environment',
  command_line: 'command line',
}

/** When a change takes effect, when that is not at once. */
export const EFFECT_LABELS: Readonly<Record<SettingEntry['effect'], string | null>> = {
  live: null,
  next_connection: 'on the next connection',
  next_device: 'on the next device',
  restart: 'after a restart',
}

let made = 0

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

type Control = { el: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement; value(): SettingValue }

function control(rule: RuleSpec, value: SettingValue): Control {
  if (rule.kind === 'flag') {
    const input = element('input', 'smv-switch')
    input.type = 'checkbox'
    input.setAttribute('role', 'switch')
    input.checked = value === true
    return { el: input, value: () => input.checked }
  }
  if (rule.kind === 'whole') {
    const input = element('input', 'smv-input')
    input.type = 'number'
    input.min = String(rule.low)
    input.max = String(rule.high)
    input.step = '1'
    input.value = String(value)
    // Not a number is sent as the text it is, so the server says what is wrong with it rather than the page guessing.
    return { el: input, value: () => (/^-?\d+$/.test(input.value.trim()) ? Number(input.value) : input.value) }
  }
  if (rule.kind === 'choice') {
    const select = element('select', 'smv-input')
    for (const option of rule.options) select.append(new Option(option, option, false, option === value))
    return { el: select, value: () => select.value }
  }
  if (rule.kind === 'origins') {
    const area = element('textarea', 'smv-input')
    area.rows = 3
    area.placeholder = 'http://localhost:3000 — one per line'
    area.value = Array.isArray(value) ? value.join('\n') : ''
    return {
      el: area,
      value: () => area.value.split(/[\n,]/).map((line) => line.trim()).filter(Boolean),
    }
  }
  const input = element('input', 'smv-input')
  input.type = 'text'
  input.maxLength = rule.max_length
  input.placeholder = rule.example
  input.value = String(value)
  input.spellcheck = false
  return { el: input, value: () => input.value.trim() }
}

export function renderField(entry: SettingEntry, options: FieldOptions): Field {
  made += 1
  const id = `smv-setting-${made}`
  const row = element('div', 'smv-field')
  row.dataset.smvSetting = entry.path

  const label = element('label', 'smv-field-name', entry.path)
  label.htmlFor = id
  const doc = element('p', 'smv-field-doc', entry.doc)
  doc.id = `${id}-doc`

  const input = control(entry.rule, entry.value)
  input.el.id = id
  input.el.setAttribute('aria-describedby', doc.id)
  input.el.disabled = !options.editable || entry.locked !== null
  input.el.addEventListener('input', () => options.onInput())
  input.el.addEventListener('change', () => options.onInput())

  const meta = element('div', 'smv-field-meta')
  meta.append(element('span', 'smv-chip', `from ${entry.origin.detail && entry.origin.layer === 'environment'
    ? entry.origin.detail : ORIGIN_LABELS[entry.origin.layer]}`))
  const effect = EFFECT_LABELS[entry.effect]
  if (effect) meta.append(element('span', 'smv-chip', `applies ${effect}`))
  if (entry.reach === 'global') meta.append(element('span', 'smv-chip', 'whole daemon'))
  if (entry.sensitive) meta.append(element('span', 'smv-chip smv-chip-sensitive', 'confirm at the terminal'))
  // A value config.toml holds can be put back; a default has nothing to put back, and a locked one is not the file's.
  if (options.editable && entry.locked === null && entry.origin.layer !== 'default') {
    const reset = element('button', 'smv-link', 'Reset')
    reset.type = 'button'
    reset.title = `Put ${entry.path} back to what applies without it`
    reset.addEventListener('click', () => options.onReset(entry.path))
    meta.append(reset)
  }

  row.append(label, doc, input.el, meta)
  if (entry.locked !== null) {
    const locked = element('p', 'smv-field-locked', entry.locked)
    const command = element('code', 'smv-field-command', entry.command)
    locked.append(' ', command)
    row.append(locked)
  }
  const error = element('p', 'smv-field-error')
  error.setAttribute('role', 'alert')
  error.hidden = true
  row.append(error)

  const initial = JSON.stringify(entry.value)
  return {
    el: row,
    entry,
    value: input.value,
    dirty: () => JSON.stringify(input.value()) !== initial,
    setError(message) {
      error.textContent = message ?? ''
      error.hidden = message === null
      row.classList.toggle('smv-field-invalid', message !== null)
      input.el.setAttribute('aria-invalid', String(message !== null))
    },
  }
}
