// SPDX-License-Identifier: Apache-2.0
/** The viewer's styles, put where a viewer is drawn: into its shadow root, or once into the document's head. */
import css from './styles.css?inline'

export const STYLE_ID = 'sim-mirror-styles'
export const VIEWER_CSS: string = css

const adopted = new WeakSet<Document | ShadowRoot>()

export function adoptStyles(node: Node): void {
  const root = node.getRootNode()
  const target = root instanceof ShadowRoot ? root : document
  if (adopted.has(target)) return
  adopted.add(target)
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = VIEWER_CSS
  if (target instanceof ShadowRoot) target.prepend(style)
  else document.head.appendChild(style)
}
