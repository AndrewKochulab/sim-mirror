// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest'
import * as viewer from './index'

describe('the package', () => {
  it('exports the viewer, its transport, its element, its icons and styles, and the protocol', () => {
    expect(typeof viewer.createViewer).toBe('function')
    expect(typeof viewer.createHttpTransport).toBe('function')
    expect(typeof viewer.TransportError).toBe('function')
    expect(typeof viewer.defineSimMirrorElement).toBe('function')
    expect(typeof viewer.SimMirrorElement).toBe('function')
    expect(viewer.ELEMENT_NAME).toBe('sim-mirror')
    expect(viewer.ICON_NAMES.length).toBeGreaterThan(0)
    expect(typeof viewer.lucideSvg).toBe('function')
    // Tests load CSS as empty text (vitest's css off); the builds inline styles.css itself.
    expect(typeof viewer.VIEWER_CSS).toBe('string')
    expect(viewer.PROTOCOL_VERSION).toBe(1)
    expect(viewer.CLOSE_RESTARTING).toBe(4412)
  })
})
