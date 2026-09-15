// SPDX-License-Identifier: Apache-2.0
/**
 * `<sim-mirror>`: the viewer as a custom element, for a page that would rather write markup than script.
 *
 *     <sim-mirror server="http://127.0.0.1:7466" scope="demo" token="…" placement="page"></sim-mirror>
 *
 * It draws in an open shadow root with its own styles, themed by `--sim-mirror-*` custom properties and styled further
 * through `::part(bar)`, `::part(stage)` and `::part(screen)`. `token` is a viewer token -- what an embed ticket is
 * spent for -- or absent where the page's own cookie or origin lets it in; a host with its own API client sets the
 * `transport` property instead. It tells the page what happens with `sim-mirror:state`, `sim-mirror:place` and
 * `sim-mirror:close` events.
 */
import { createHttpTransport } from './http-transport'
import type { SimMirrorTransport } from './transport'
import { createViewer, type Placement, type ViewHandle } from './viewer'

export const ELEMENT_NAME = 'sim-mirror'
const PLACEMENTS: readonly Placement[] = ['dock', 'window', 'page']

const placementOf = (value: string | null): Placement =>
  PLACEMENTS.includes(value as Placement) ? (value as Placement) : 'page'

export class SimMirrorElement extends HTMLElement {
  static readonly observedAttributes = ['server', 'scope', 'token', 'placement']

  #view: ViewHandle | null = null
  #transport: SimMirrorTransport | null = null

  constructor() {
    super()
    this.attachShadow({ mode: 'open' })
  }

  get view(): ViewHandle | null {
    return this.#view
  }

  get transport(): SimMirrorTransport | null {
    return this.#transport
  }

  set transport(value: SimMirrorTransport | null) {
    this.#transport = value
    if (this.isConnected) this.#render()
  }

  connectedCallback(): void {
    this.#render()
  }

  disconnectedCallback(): void {
    this.#view?.destroy()
    this.#view = null
  }

  attributeChangedCallback(name: string, previous: string | null, value: string | null): void {
    if (previous === value || !this.isConnected) return
    if (name === 'placement' && this.#view) this.#view.setPlacement(placementOf(value))
    else this.#render()
  }

  #tell(type: string, detail?: unknown): void {
    this.dispatchEvent(new CustomEvent(`sim-mirror:${type}`, { detail, bubbles: true, composed: true }))
  }

  #render(): void {
    this.#view?.destroy()
    this.#view = null
    const scope = this.getAttribute('scope')
    const token = this.getAttribute('token')
    const transport = this.#transport
      ?? (scope ? createHttpTransport({ baseUrl: this.getAttribute('server') ?? '', scope, token: () => token }) : null)
    if (!transport) return
    const placement = placementOf(this.getAttribute('placement'))
    this.#view = createViewer(this.shadowRoot!, {
      transport,
      placement,
      onPlace: placement === 'page' ? undefined : (next) => this.#tell('place', { placement: next }),
      onClose: () => this.#tell('close'),
      onState: (state) => this.#tell('state', state),
    })
    this.#view.setActive(true)
  }
}

/** Define `<sim-mirror>` -- or another name -- once; a name already taken is left alone. */
export function defineSimMirrorElement(name: string = ELEMENT_NAME, registry: CustomElementRegistry = customElements): void {
  if (!registry.get(name)) registry.define(name, class extends SimMirrorElement {})
}
