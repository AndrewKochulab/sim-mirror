// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from 'vitest'

import { createLayers, createPopover, type DismissReason, type PopoverOptions } from './popover'

/** A viewer's root holding a screen, a toggle and a menu of three rows, the middle one checked. */
function build(parent: Node = document.body, overrides: Partial<PopoverOptions> = {}) {
  const root = document.createElement('div')
  root.innerHTML = `
    <canvas tabindex="0" data-screen></canvas>
    <button type="button" data-toggle aria-expanded="false">Devices</button>
    <div data-panel role="menu" hidden>
      <button type="button" role="menuitemradio" aria-checked="false">One</button>
      <button type="button" role="menuitemradio" aria-checked="true">Two</button>
      <button type="button" role="menuitem">Three</button>
    </div>`
  parent.appendChild(root)
  const q = <T extends HTMLElement>(selector: string) => root.querySelector<T>(selector)!
  const screen = q('[data-screen]')
  const toggle = q('[data-toggle]')
  const panel = q('[data-panel]')
  const rows = [...panel.querySelectorAll<HTMLButtonElement>('button')]
  const layers = createLayers()
  const closed: DismissReason[] = []
  const popover = createPopover({ root, toggle, panel, layers, onClose: (reason) => closed.push(reason), ...overrides })
  /** What the screen's own handler heard: it stops every key from going further, as the real one does. */
  const heard: string[] = []
  screen.addEventListener('keydown', (event) => {
    event.stopPropagation()
    heard.push(event.key)
  })
  const key = (target: HTMLElement, name: string) => {
    const event = new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true, composed: true })
    target.dispatchEvent(event)
    return event
  }
  const press = (target: EventTarget) =>
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, composed: true }))
  return { root, screen, toggle, panel, rows, layers, closed, popover, heard, key, press }
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('layers', () => {
  it('are open while anything has been added and not removed', () => {
    const layers = createLayers()
    const [menu, panel] = [{}, {}]
    expect(layers.open).toBe(false)
    layers.add(menu)
    layers.add(panel)
    layers.remove(menu)
    expect(layers.open).toBe(true)
    layers.remove(panel)
    layers.remove(panel)
    expect(layers.open).toBe(false)
  })
})

describe('a popover', () => {
  it('opens and closes once each way, saying so on its toggle and in its layers', () => {
    const { popover, panel, toggle, layers, closed } = build()
    popover.open()
    popover.open()
    expect([popover.isOpen, panel.hidden, toggle.getAttribute('aria-expanded'), layers.open]).toEqual([
      true, false, 'true', true,
    ])
    popover.close()
    popover.close()
    expect([popover.isOpen, panel.hidden, toggle.getAttribute('aria-expanded'), layers.open]).toEqual([
      false, true, 'false', false,
    ])
    expect(closed).toEqual(['api'])
  })

  it('closes on Escape from the screen before the screen hears it, and gives focus back to its toggle', () => {
    const { popover, screen, toggle, heard, closed, key } = build()
    popover.open()
    screen.focus()
    const escape = key(screen, 'Escape')
    expect([escape.defaultPrevented, heard, closed]).toEqual([true, [], ['escape']])
    expect(document.activeElement).toBe(toggle)
    // Closed, the Escape is the screen's again.
    const again = key(screen, 'Escape')
    expect([again.defaultPrevented, heard]).toEqual([false, ['Escape']])
  })

  it('leaves every other key to whoever it was for', () => {
    const { popover, screen, heard, key } = build()
    popover.open()
    expect(key(screen, 'a').defaultPrevented).toBe(false)
    expect(heard).toEqual(['a'])
  })

  it('walks its rows with the arrows, Home and End, wrapping round and skipping hidden rows', () => {
    const { popover, screen, rows, heard, key } = build()
    popover.open()
    const at = () => rows.indexOf(document.activeElement as HTMLButtonElement)
    expect(key(screen, 'ArrowDown').defaultPrevented).toBe(true)
    expect(at()).toBe(0)
    key(rows[0], 'ArrowUp')
    expect(at()).toBe(2)
    key(rows[2], 'ArrowDown')
    expect(at()).toBe(0)
    rows[1].hidden = true
    key(rows[0], 'ArrowDown')
    expect(at()).toBe(2)
    key(rows[2], 'Home')
    expect(at()).toBe(0)
    key(rows[0], 'End')
    expect(at()).toBe(2)
    screen.focus()
    key(screen, 'ArrowUp')
    expect(at()).toBe(2)
    expect(heard).toEqual([])
  })

  it('walks nothing when it has no rows', () => {
    const { popover, panel, screen, key } = build()
    panel.innerHTML = '<p>Looking for simulators…</p>'
    popover.open()
    screen.focus()
    key(screen, 'ArrowDown')
    popover.focusItem('checked')
    expect(document.activeElement).toBe(screen)
  })

  it('focuses the checked row, else the first, or the last', () => {
    const { popover, rows } = build()
    popover.focusItem('checked')
    expect(document.activeElement).toBe(rows[1])
    popover.focusItem('last')
    expect(document.activeElement).toBe(rows[2])
    rows[1].setAttribute('aria-checked', 'false')
    popover.focusItem('checked')
    expect(document.activeElement).toBe(rows[0])
    popover.focusItem('first')
    expect(document.activeElement).toBe(rows[0])
  })

  it('closes when Tab moves on from the menu, but not on a Tab from the screen, which it would then hear', () => {
    const { popover, screen, rows, toggle, closed, heard, key } = build()
    popover.open()
    key(screen, 'Tab')
    expect([popover.isOpen, heard]).toEqual([true, ['Tab']])
    const tab = key(rows[0], 'Tab')
    expect([popover.isOpen, tab.defaultPrevented, closed]).toEqual([false, false, ['tab']])
    popover.open()
    key(toggle, 'Tab')
    expect(closed).toEqual(['tab', 'tab'])
  })

  it('closes on a press outside itself and its toggle, after whatever was pressed has heard it', () => {
    const { popover, screen, rows, toggle, closed, press, layers } = build()
    const heldWhenPressed: boolean[] = []
    screen.addEventListener('pointerdown', () => heldWhenPressed.push(layers.open))
    press(screen)
    expect(closed).toEqual([])
    popover.open()
    press(rows[0])
    press(toggle)
    expect(popover.isOpen).toBe(true)
    press(screen)
    expect([popover.isOpen, closed, heldWhenPressed]).toEqual([false, ['outside'], [false, true]])
  })

  it('works inside a shadow root: focus, presses and keys are read where the viewer lives', () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const shadow = host.attachShadow({ mode: 'open' })
    const { popover, screen, rows, closed, key, press } = build(shadow)
    popover.open()
    rows[0].focus()
    key(rows[0], 'ArrowDown')
    expect(shadow.activeElement).toBe(rows[1])
    press(rows[1])
    expect(popover.isOpen).toBe(true)
    press(document.body)
    expect(closed).toEqual(['outside'])
    popover.open()
    key(screen, 'Escape')
    expect(closed).toEqual(['outside', 'escape'])
  })

  it('walks only the rows it is told to', () => {
    const { popover, rows, screen, key } = build(document.body, { items: '[aria-checked]' })
    popover.open()
    key(screen, 'End')
    expect(document.activeElement).toBe(rows[1])
  })

  it('as a dialog, leaves the arrows and Tab to its fields, and may stay open on a press outside', () => {
    const { popover, rows, screen, closed, key, press } = build(document.body, { menu: false, outside: false })
    popover.open()
    rows[0].focus()
    expect(key(rows[0], 'ArrowDown').defaultPrevented).toBe(false)
    expect(document.activeElement).toBe(rows[0])
    key(rows[0], 'Tab')
    press(screen)
    expect([popover.isOpen, closed]).toEqual([true, []])
    key(rows[0], 'Escape')
    expect(closed).toEqual(['escape'])
  })

  it('hears nothing once destroyed, and leaves nothing open', () => {
    const onClose = vi.fn()
    const { popover, screen, layers, key, press } = build(document.body, { onClose })
    popover.open()
    popover.destroy()
    key(screen, 'Escape')
    press(document.body)
    expect([onClose.mock.calls.length, layers.open]).toEqual([0, false])
  })
})
