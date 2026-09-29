// SPDX-License-Identifier: Apache-2.0
/**
 * A note over the screen: what a person's action did, or why not -- a recording kept, a display change refused. It
 * stays until it is closed with its own button or replaced by the next, and is read out as it changes.
 */
import { escapeHTML } from './escape'
import type { IconRenderer } from './icons'

export interface Note {
  /** Show markup the caller made safe; `sayText` for plain text. */
  show(markup: string): void
  sayText(text: string): void
  hide(): void
  destroy(): void
}

export function createNote(el: HTMLElement, icon: IconRenderer): Note {
  el.setAttribute('role', 'status')

  function show(markup: string): void {
    el.innerHTML = `${markup}<button type="button" class="smv-icon smv-note-close" data-smv-note-close title="Close"`
      + ` aria-label="Close">${icon('close')}</button>`
    el.hidden = false
  }

  const onClick = (event: Event) => {
    if ((event.target as HTMLElement).closest('[data-smv-note-close]')) el.hidden = true
  }
  el.addEventListener('click', onClick)

  return {
    show,
    sayText: (text) => show(`<p>${escapeHTML(text)}</p>`),
    hide: () => {
      el.hidden = true
    },
    destroy: () => el.removeEventListener('click', onClick),
  }
}
