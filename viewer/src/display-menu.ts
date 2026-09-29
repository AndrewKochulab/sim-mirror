// SPDX-License-Identifier: Apache-2.0
/**
 * The Display menu: how the device looks for a demo -- a demo status bar, its text size, increased contrast and, on a
 * real device, reduced motion -- changed as an agent's `sim_device` changes them, and put back the same way when the
 * device is let go. A row is offered only when the device's connector can make its change.
 *
 * The device is not asked how it looks, so the menu starts from how a device comes -- its own status bar, Large text,
 * standard contrast -- and follows what it is asked to change here. What the device did, or why not, is said in the
 * note over the screen.
 */
import { escapeHTML } from './escape'
import type { Note } from './note'
import { createPopover, type Layers } from './popover'
import type { Capability } from './protocol.generated'
import type { DeviceChange } from './transport'

/** The text sizes a person steps through, smallest first, as `sim_device` names them. */
export const TEXT_SIZES = [
  'extra-small', 'small', 'medium', 'large', 'extra-large', 'extra-extra-large', 'extra-extra-extra-large',
] as const
const DEFAULT_SIZE = TEXT_SIZES.indexOf('large')

export interface DisplayMenuOptions {
  root: HTMLElement
  toggle: HTMLButtonElement
  panel: HTMLElement
  layers: Layers
  note: Note
  changeDevice(change: DeviceChange): Promise<string>
}

export interface DisplayMenu {
  /** What the device's connector can change, and whether it is a real device: the rows follow. */
  offer(capabilities: readonly Capability[], physical: boolean): void
  destroy(): void
}

interface Row {
  id: string
  label: string
  /** For a switch: whether it is on. */
  checked?: boolean
  change(): DeviceChange
  /** What changes here once the device has made the change. */
  done(): void
}

export function createDisplayMenu(options: DisplayMenuOptions): DisplayMenu {
  const { toggle, panel, note } = options
  let offered: { statusBar: boolean, accessibility: boolean, motion: boolean } = {
    statusBar: false, accessibility: false, motion: false,
  }
  let demo = false
  let size = DEFAULT_SIZE
  let contrast = false
  let reduced = false

  const menu = createPopover({ root: options.root, toggle, panel, layers: options.layers })

  function rows(): Row[] {
    const found: Row[] = []
    if (offered.statusBar) {
      found.push({
        id: 'status', label: 'Demo status bar', checked: demo,
        change: () => ({ action: 'status_bar', preset: demo ? 'clear' : 'demo' }), done: () => { demo = !demo },
      })
    }
    if (offered.accessibility) {
      const step = (by: 1 | -1): Row => ({
        id: by > 0 ? 'larger' : 'smaller',
        label: `${by > 0 ? 'Larger' : 'Smaller'} text`,
        change: () => ({ action: 'text_size', size: TEXT_SIZES[size + by] }),
        done: () => { size += by },
      })
      if (size < TEXT_SIZES.length - 1) found.push(step(1))
      if (size > 0) found.push(step(-1))
      found.push({
        id: 'contrast', label: 'Increase contrast', checked: contrast,
        change: () => ({ action: 'contrast', on: !contrast }), done: () => { contrast = !contrast },
      })
    }
    if (offered.motion) {
      found.push({
        id: 'motion', label: 'Reduce motion', checked: reduced,
        change: () => ({ action: 'reduce_motion', on: !reduced }), done: () => { reduced = !reduced },
      })
    }
    return found
  }

  function paint(): void {
    panel.innerHTML = rows().map((row) => {
      const role = row.checked === undefined ? 'menuitem' : 'menuitemcheckbox'
      const checked = row.checked === undefined ? '' : ` aria-checked="${row.checked}"`
      return `<button type="button" role="${role}"${checked} data-smv-display="${row.id}">${escapeHTML(row.label)}</button>`
    }).join('') + `<p class="smv-picker-note">Text: ${TEXT_SIZES[size].split('-').join(' ')}</p>`
  }

  async function choose(id: string): Promise<void> {
    const row = rows().find((candidate) => candidate.id === id)
    if (!row) return
    try {
      note.sayText(await options.changeDevice(row.change()))
      row.done()
    } catch (error) {
      note.sayText((error as Error).message || 'The device did not change.')
    }
    if (menu.isOpen) {
      paint()
      panel.querySelector<HTMLElement>(`[data-smv-display="${id}"]`)?.focus()
    }
  }

  const onToggle = () => {
    if (menu.isOpen) return menu.close('toggle')
    menu.open()
    paint()
    menu.focusItem('first')
  }
  const onPanel = (event: Event) => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-smv-display]')
    if (target) void choose(target.dataset.smvDisplay!)
  }
  toggle.addEventListener('click', onToggle)
  panel.addEventListener('click', onPanel)

  return {
    offer(capabilities, physical) {
      offered = {
        statusBar: capabilities.includes('status_bar'),
        accessibility: capabilities.includes('accessibility'),
        motion: physical && capabilities.includes('accessibility'),
      }
      toggle.hidden = !(offered.statusBar || offered.accessibility)
      if (toggle.hidden) menu.close('api')
      else if (menu.isOpen) paint()
    },
    destroy() {
      toggle.removeEventListener('click', onToggle)
      panel.removeEventListener('click', onPanel)
      menu.destroy()
    },
  }
}
