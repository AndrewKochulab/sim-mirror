# The web component and the viewer library

The npm package `@andrewkochulab/sim-mirror` is the viewer: a `<sim-mirror>` custom element for pages that would rather
write markup, and a `createViewer` function for applications that manage their own DOM. Both draw a scope's live
screen, take a person's input, and show the agent's cursor.

[examples/web-component](../../examples/web-component/) is a complete page.

## Install

Until it is published to npm, the package is attached to each release:

```sh
npm install https://github.com/AndrewKochulab/sim-mirror/releases/download/v0.1.0/andrewkochulab-sim-mirror-0.1.0.tgz
```

It is an ES module with TypeScript types and no runtime dependencies; its styles are built in.

## The element

```html
<sim-mirror server="http://127.0.0.1:7466" scope="demo" token="…" placement="page"></sim-mirror>

<script type="module">
  import { defineSimMirrorElement } from '@andrewkochulab/sim-mirror'
  defineSimMirrorElement()   // or defineSimMirrorElement('my-simulator')
</script>
```

| Attribute | Meaning |
|---|---|
| `server` | The SimMirror server, such as `http://127.0.0.1:7466`; the page's own origin when absent |
| `scope` | The scope whose simulator it shows; nothing is shown without one |
| `token` | A viewer token for the scope, sent as `Authorization: Bearer`; absent where the page's own origin or cookie lets it in |
| `placement` | `page` (the default), `dock` or `window` -- where the host shows it, which decides the buttons offered |

Changing `server`, `scope` or `token` starts the viewer afresh; changing `placement` does not reconnect. Removing the
element closes its socket. It draws in an open shadow root.

| Event | `detail` |
|---|---|
| `sim-mirror:state` | `{device, connector, capabilities, viewOnly, connected, encoding}` whenever one of them changes |
| `sim-mirror:place` | `{placement}`: a person asked to move the viewer to a dock or a window (not offered on a page) |
| `sim-mirror:close` | `null`: a person closed the viewer |

Events bubble and cross the shadow boundary.

### Getting a token without putting one in the page

The page's backend mints a one-shot [embed ticket](iframe.md#2-mint-a-ticket-on-your-backend); the page spends it for
a viewer token that lives only in its memory:

```js
const { server, scope, ticket } = await (await fetch('/api/embed-ticket', { method: 'POST' })).json()
const exchanged = await fetch(`${server}/api/v1/auth/exchange`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ code: ticket }),
})
const { data } = await exchanged.json()
element.setAttribute('server', server)
element.setAttribute('token', data.token)
element.setAttribute('scope', scope)
```

The daemon must allow the page's origin, for its API and its screen socket:

```toml
[security]
allowed_origins = ["https://my-app.example"]
```

## Theming

Custom properties pass into the shadow root. Each has a default.

| Property | Styles |
|---|---|
| `--sim-mirror-accent`, `--sim-mirror-on-accent` | Active controls, and text on them |
| `--sim-mirror-surface`, `--sim-mirror-surface-sunken`, `--sim-mirror-border` | The bar, the stage behind the screen, and edges |
| `--sim-mirror-text`, `--sim-mirror-text-muted` | Text |
| `--sim-mirror-success`, `--sim-mirror-warning`, `--sim-mirror-danger` | The device's state |
| `--sim-mirror-radius`, `--sim-mirror-radius-full` | Corners |
| `--sim-mirror-space-1` to `--sim-mirror-space-4` | Spacing |
| `--sim-mirror-font`, `--sim-mirror-font-size` | Type |

For anything more, style its parts: `sim-mirror::part(bar)`, `::part(stage)` and `::part(screen)`.

## The function

```ts
import { createHttpTransport, createViewer } from '@andrewkochulab/sim-mirror'

const view = createViewer(document.querySelector('#simulator')!, {
  transport: createHttpTransport({ baseUrl: 'http://127.0.0.1:7466', scope: 'demo', token: () => viewerToken }),
  placement: 'page',
  onState: (state) => console.log(state.device?.state, state.viewOnly),
})
view.setActive(true)
```

`createViewer(host, options)` draws into an element or a shadow root and answers a handle.

| Option | Meaning |
|---|---|
| `transport` | How it reaches its server (required) |
| `placement` | `page`, `dock` or `window` |
| `onPlace(next)` | Offer moving between a dock and a window |
| `pageHref` | Offer opening the viewer on a page of its own |
| `onClose()` | Offer closing it |
| `onState(state)` | Told whenever the device, the connector's offer or the connection changes |
| `reducedMotion()` | Whether the agent's cursor jumps instead of gliding; the system setting when absent |
| `canDecodeH264()` | Whether to ask for H.264; WebCodecs in a secure page when absent |
| `icon(name)` | Draws the viewer's icons; Lucide's, inline, when absent |

| Handle | Does |
|---|---|
| `el` | The viewer's root element, which a host may move between containers without reconnecting |
| `device`, `hello`, `encoding` | The device, what the server offered, and the encoding in use |
| `connected()` | Whether the screen socket is open |
| `setActive(on)` | Connect while active; let the screen go while not |
| `setPlacement(placement)`, `focus()` | Change where it is shown; focus the screen for keys |
| `destroy()` | Close the socket and remove the viewer |

## Transports

`createHttpTransport(options)` talks to a SimMirror daemon, or to any host that mounts SimMirror's routers:

| Option | Meaning |
|---|---|
| `scope` | The scope (required) |
| `baseUrl` | The server; the page's origin when empty |
| `token()` | The bearer token for each request, asked for every time so it can change; none when absent |
| `prefix` | Where the scope routes are mounted: `/api/v1/scopes` on the daemon |
| `fetch`, `origin` | A `fetch` of your own, and the origin a relative `baseUrl` resolves against |

A refusal rejects with a `TransportError` carrying the server's message and status.

A host with its own API client passes an object of this shape instead:

```ts
interface SimMirrorTransport {
  status(): Promise<ScopeStatus>
  start(): Promise<Started>                 // brings the device up and mints a screen socket ticket
  stop(shutdown: boolean): Promise<boolean>
  devices(): Promise<DeviceChoice[]>
  choose(udid: string): Promise<void>
  socketUrl(ticket: string): string
}
```

## Encodings and view-only

On connecting, the server says what it offers and the viewer answers with what it decodes: H.264 first where WebCodecs
can decode it in a secure page (HTTPS, `http://127.0.0.1` or `http://localhost`) and it has not failed on this page,
then JPEG. A connector that cannot touch the screen makes the viewer a mirror: it says so, offers no buttons it cannot
press, and sends no input.
