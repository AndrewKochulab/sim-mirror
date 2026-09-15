// SPDX-License-Identifier: Apache-2.0
/** `@andrewkochulab/sim-mirror`: the SimMirror viewer, as a function, a custom element, and the protocol's types. */
export { createViewer } from './viewer'
export type { Placement, ViewerOptions, ViewerState, ViewHandle } from './viewer'
export { createHttpTransport, TransportError } from './http-transport'
export type { HttpTransportOptions } from './http-transport'
export type { SimMirrorTransport } from './transport'
export { defineSimMirrorElement, ELEMENT_NAME, SimMirrorElement } from './element'
export { ICON_NAMES, lucideSvg } from './icons'
export type { IconName, IconRenderer } from './icons'
export { VIEWER_CSS } from './styles'
export * from './protocol.generated'
