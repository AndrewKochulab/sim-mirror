// SPDX-License-Identifier: Apache-2.0
/**
 * Loaded before every suite. jsdom has no `PointerEvent`, which the viewer listens for; one built on `MouseEvent`
 * carries what the viewer reads -- where, which button, and which pointer.
 */
if (typeof globalThis.PointerEvent === 'undefined') {
  class PointerEventPolyfill extends MouseEvent {
    readonly pointerId: number

    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init)
      this.pointerId = init.pointerId ?? 1
    }
  }
  Object.defineProperty(globalThis, 'PointerEvent', { configurable: true, writable: true, value: PointerEventPolyfill })
}
