# Web component

The `<sim-mirror>` element on a page of another origin, themed to match it.

The page calls the daemon itself, so the daemon must allow its origin (`sim-mirror config path` shows the file):

```toml
[security]
allowed_origins = ["http://127.0.0.1:7483"]
```

Build the viewer library once, then start the page's backend with the daemon started after that change:

```sh
(cd viewer && npm ci && npm run build)
sim-mirror token create --kind viewer --scope demo --label "web component example"
SIM_MIRROR_TOKEN=<the token it printed> python3 examples/embed-host/host.py --page examples/web-component
```

and open <http://127.0.0.1:7483/>.

## The element

```html
<sim-mirror server="http://127.0.0.1:7466" scope="demo" token="…" placement="page"></sim-mirror>
```

| Attribute | Meaning |
|---|---|
| `server` | The daemon's address; the page's own origin when absent |
| `scope` | The scope whose simulator it shows |
| `token` | A viewer token for that scope -- here spent from a one-shot ticket, and held only in memory |
| `placement` | `page`, `dock` or `window`; where the host shows it, which decides the buttons offered |

A host with its own API sets the element's `transport` property instead of `server` and `token` (see
`SimMirrorTransport`).

| Event | `detail` |
|---|---|
| `sim-mirror:state` | `{device, connector, capabilities, viewOnly, connected}` whenever one of them changes |
| `sim-mirror:place` | `{placement}` a person asked for |
| `sim-mirror:close` | `null`, when a person closed the viewer |

Theme it with `--sim-mirror-accent`, `-on-accent`, `-surface`, `-surface-sunken`, `-border`, `-text`, `-text-muted`,
`-success`, `-warning`, `-danger`, `-radius`, `-radius-full`, `-space-1` to `-space-4`, `-font` and `-font-size`, and
style its parts (`::part(screen)`, `::part(stage)`) for anything more.

From an origin not in `allowed_origins`, the daemon sends no CORS headers and closes the screen socket, so the element
shows nothing.
