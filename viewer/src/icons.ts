// SPDX-License-Identifier: Apache-2.0
/**
 * The viewer's icons: Lucide's (ISC; see THIRD_PARTY_LICENSES.md), inlined as SVG, so the viewer loads nothing from a
 * CDN and draws inside a shadow root. A host that draws icons its own way passes `icon` to `createViewer`.
 */

export type IconName = 'home' | 'lock' | 'moon' | 'sun' | 'devices' | 'undock' | 'dock' | 'page' | 'power' | 'close'
  | 'pointer' | 'settings'

/** Draws an icon as markup. */
export type IconRenderer = (name: IconName) => string

type Shape = [tag: 'path' | 'rect' | 'circle', attributes: Record<string, string>]

/** Each icon's shapes, as Lucide 0.460 draws them: circle, lock, moon, sun, smartphone, picture-in-picture-2, panel-right,
 * external-link, power, x, mouse-pointer-2 and settings. */
const SHAPES: Record<IconName, Shape[]> = {
  home: [['circle', { cx: '12', cy: '12', r: '10' }]],
  lock: [
    ['rect', { width: '18', height: '11', x: '3', y: '11', rx: '2', ry: '2' }],
    ['path', { d: 'M7 11V7a5 5 0 0 1 10 0v4' }],
  ],
  moon: [['path', { d: 'M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z' }]],
  sun: [
    ['circle', { cx: '12', cy: '12', r: '4' }],
    ['path', { d: 'M12 2v2' }], ['path', { d: 'M12 20v2' }], ['path', { d: 'm4.93 4.93 1.41 1.41' }],
    ['path', { d: 'm17.66 17.66 1.41 1.41' }], ['path', { d: 'M2 12h2' }], ['path', { d: 'M20 12h2' }],
    ['path', { d: 'm6.34 17.66-1.41 1.41' }], ['path', { d: 'm19.07 4.93-1.41 1.41' }],
  ],
  devices: [
    ['rect', { width: '14', height: '20', x: '5', y: '2', rx: '2', ry: '2' }],
    ['path', { d: 'M12 18h.01' }],
  ],
  undock: [
    ['path', { d: 'M21 9V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v10c0 1.1.9 2 2 2h4' }],
    ['rect', { width: '10', height: '7', x: '12', y: '13', rx: '2' }],
  ],
  dock: [
    ['rect', { width: '18', height: '18', x: '3', y: '3', rx: '2' }],
    ['path', { d: 'M15 3v18' }],
  ],
  page: [
    ['path', { d: 'M15 3h6v6' }], ['path', { d: 'M10 14 21 3' }],
    ['path', { d: 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6' }],
  ],
  power: [['path', { d: 'M12 2v10' }], ['path', { d: 'M18.4 6.6a9 9 0 1 1-12.77.04' }]],
  close: [['path', { d: 'M18 6 6 18' }], ['path', { d: 'm6 6 12 12' }]],
  pointer: [[
    'path',
    { d: 'M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z' },
  ]],
  settings: [
    ['path', {
      d: 'M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z',
    }],
    ['circle', { cx: '12', cy: '12', r: '3' }],
  ],
}

export const ICON_NAMES = Object.keys(SHAPES) as IconName[]

/** An icon as Lucide's own SVG: 24 units square, stroked in the current colour. */
export function lucideSvg(name: IconName): string {
  const body = SHAPES[name]
    .map(([tag, attributes]) => `<${tag} ${Object.entries(attributes).map(([key, value]) => `${key}="${value}"`).join(' ')}/>`)
    .join('')
  return '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"'
    + ' stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"'
    + ` data-icon="${name}">${body}</svg>`
}
