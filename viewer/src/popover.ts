// SPDX-License-Identifier: Apache-2.0
/**
 * A menu or panel that opens over the screen: shown and hidden with its toggle's `aria-expanded`, and closed by Escape
 * or -- unless told otherwise -- by a press anywhere outside it. A menu is also closed by Tab and walked with the arrow keys, Home and End; a dialog
 * (`menu: false`) leaves those keys to its own fields.
 *
 * While any one is open its `Layers` say so, and the screen's input holds back (`viewer-input.ts`): a key typed into an
 * open menu, or the press that closes it, is the person talking to the menu, not to the device. Escape is heard in the
 * capture phase on the viewer's root, before the screen's own handler, which keeps every key it sees from the page.
 */

/** Why an open popover closed. Escape gives focus back to the toggle; the others leave it where the person put it. */
export type DismissReason = 'escape' | 'outside' | 'tab' | 'toggle' | 'api'

/** What is open over the screen right now. */
export interface Layers {
  readonly open: boolean
  add(layer: object): void
  remove(layer: object): void
}

export function createLayers(): Layers {
  const layers = new Set<object>()
  return {
    get open() {
      return layers.size > 0
    },
    add: (layer) => void layers.add(layer),
    remove: (layer) => void layers.delete(layer),
  }
}

export interface PopoverOptions {
  /** The viewer's root: keys anywhere inside it reach an open popover first. */
  root: HTMLElement
  toggle: HTMLElement
  panel: HTMLElement
  layers: Layers
  /** Which of the panel's elements the arrow keys walk. */
  items?: string
  /** A menu, whose rows the arrow keys walk and which Tab closes; true when absent. */
  menu?: boolean
  /** Closed by a press outside it; true when absent. A dialog holding a half-made change is not. */
  outside?: boolean
  onClose?(reason: DismissReason): void
}

export interface Popover {
  readonly isOpen: boolean
  open(): void
  close(reason?: DismissReason): void
  /** Focus an item: the checked one, else the first, or the first or last. Nothing when there is none. */
  focusItem(which: 'checked' | 'first' | 'last'): void
  destroy(): void
}

const MENU_ITEMS = '[role^="menuitem"]'

export function createPopover(
  { root, toggle, panel, layers, items = MENU_ITEMS, menu = true, outside = true, onClose }: PopoverOptions,
): Popover {
  const doc = root.ownerDocument
  let isOpen = false

  const itemsIn = () => [...panel.querySelectorAll<HTMLElement>(items)].filter((item) => !item.hidden)

  /** The element that has focus, looking into the shadow root the viewer may live in. */
  function focused(): Element | null {
    const node = root.getRootNode() as Document | ShadowRoot
    return node.activeElement
  }

  function focusItem(which: 'checked' | 'first' | 'last'): void {
    const found = itemsIn()
    const item = which === 'last'
      ? found[found.length - 1]
      : (which === 'checked' && found.find((candidate) => candidate.getAttribute('aria-checked') === 'true')) || found[0]
    item?.focus()
  }

  function step(by: 1 | -1): void {
    const found = itemsIn()
    if (!found.length) return
    const at = found.indexOf(focused() as HTMLElement)
    const next = at < 0 ? (by > 0 ? 0 : found.length - 1) : (at + by + found.length) % found.length
    found[next].focus()
  }

  function open(): void {
    if (isOpen) return
    isOpen = true
    panel.hidden = false
    toggle.setAttribute('aria-expanded', 'true')
    layers.add(popover)
  }

  function close(reason: DismissReason = 'api'): void {
    if (!isOpen) return
    isOpen = false
    panel.hidden = true
    toggle.setAttribute('aria-expanded', 'false')
    layers.remove(popover)
    if (reason === 'escape') toggle.focus()
    onClose?.(reason)
  }

  function onKey(event: KeyboardEvent): void {
    if (!isOpen) return
    const moves: Record<string, () => void> = {
      ArrowDown: () => step(1), ArrowUp: () => step(-1), Home: () => focusItem('first'), End: () => focusItem('last'),
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      close('escape')
    } else if (!menu) {
      return
    } else if (event.key === 'Tab') {
      // Only when moving on from the menu itself: closed from the screen, the screen would hear the Tab once it had.
      const target = event.target as Node
      if (panel.contains(target) || toggle.contains(target)) close('tab')
    } else if (moves[event.key]) {
      event.preventDefault()
      event.stopPropagation()
      moves[event.key]()
    }
  }

  /** In the bubble phase, so the screen hears the press first -- and, holding back while a popover is open, ignores it. */
  function onPress(event: Event): void {
    if (!isOpen || !outside) return
    const path = event.composedPath()
    if (!path.includes(panel) && !path.includes(toggle)) close('outside')
  }

  root.addEventListener('keydown', onKey, true)
  doc.addEventListener('pointerdown', onPress)

  const popover: Popover = {
    get isOpen() {
      return isOpen
    },
    open,
    close,
    focusItem,
    destroy() {
      root.removeEventListener('keydown', onKey, true)
      doc.removeEventListener('pointerdown', onPress)
      layers.remove(popover)
    },
  }
  return popover
}
