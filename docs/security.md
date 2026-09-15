# Security

SimMirror runs a daemon on your Mac that can boot simulators, touch their screens, install and launch apps, and --
when build tools are on -- run `xcodebuild`. This page is what it defends against, and how.

## What is trusted

- **You**, the macOS user who installed SimMirror, and anything running as you: it can read the admin token, as the
  CLI does.
- **The agents you give tools to**, within their scope: an agent token reaches one scope's device and the folders it
  was given.

## What is not

- **Web pages in your browser.** A server on `127.0.0.1` is reachable from every page you have open: a page can post to
  it, frame it, open sockets to it, or point its own domain at `127.0.0.1` and read it as its own.
- **Other processes' URLs and logs.** A secret in a URL or a process's arguments can leak into logs, history and `ps`.
- Anything on the network: v0.1 listens on loopback only.

Out of scope: an attacker already running code as your user, and denial of service from the local machine.

## The daemon

| Defence | Against |
|---|---|
| Listens on `127.0.0.1` only (`server.host` accepts only loopback addresses) | The network |
| **Host allowlist**: answers only for `127.0.0.1:<port>` and `localhost:<port>` | DNS rebinding |
| **Exact Origin allowlist** on state-changing requests and every WebSocket: its own origin and `security.allowed_origins`, compared by scheme, host and port | Cross-site requests and WebSocket hijacking |
| **CORS** headers only for listed origins, never `*` | Pages reading its answers |
| **`frame-ancestors`**: itself and `security.frame_ancestors` | Clickjacking |
| `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` | Content sniffing; codes leaving in a referrer |

A request with no `Origin` is not from a page -- it is a CLI or a backend -- and its token decides it. The rules are read
on every request, so a changed setting applies at once.

## Credentials

| Credential | Is | Lives |
|---|---|---|
| **Admin token** | Made on the daemon's first start; may do everything | A file readable only by you, in Application Support |
| **Scoped tokens** (`agent`, `viewer`, `admin`) | For a kind of client and a list of scopes; an agent token may name folders | Only their SHA-256, in `tokens.json` (0600); shown once when made; compared in constant time |
| **Login codes and embed tickets** | One-shot, 60 seconds, for one scope | In a URL **fragment**, which browsers never send to a server; spent at `/api/v1/auth/exchange` for a viewer token |
| **Viewer sessions** | A viewer token from a code, 12 hours | In the page's memory and the daemon's; never on disk |
| **Screen socket tickets** | One-shot, 60 seconds, for one scope and -- when minted for a page -- that page's origin | Minted only by an authenticated start |

- An agent's routes take only an agent token, for the scope it names; a person's routes take the admin token or a
  viewer credential for the scope.
- `sim-mirror mcp` passes its agent token to the relay in the environment, never argv, and revokes it when the client
  goes.
- Tokens and tickets in a query string are redacted from the server's logs.

## What an agent can reach

- **Its scope's device only**, through the tool whitelist; the screen socket accepts only the protocol's input
  messages.
- **URLs**: `sim_app open_url` refuses `file:`, `data:`, `javascript:` and `about:` URLs and the device's settings
  URLs.
- **Installs**: a built `.app` inside a folder its token names, the scope's DerivedData or Xcode's -- with every link
  followed before the check.
- **Commands**: builds and tests run `xcodebuild` only while `build.tools` is on (off by default), in the folder its
  token names, with a timeout, one at a time per scope.
- **Nothing outside the device**: every gesture is drawn by the viewer's own cursor, never the Mac's pointer.

## The relay

The MCP relay is one standard-library file. It reads its URL and credentials from the environment, talks only to
loopback addresses, and ignores proxy settings, so a tool call never leaves the machine.

## What leaves your Mac

Nothing, from SimMirror itself. What an agent sees -- snapshots, screenshots, logs -- goes to the agent's model provider
as the agent's client sends it.

## Reporting

See [SECURITY.md](../SECURITY.md) for how to report a vulnerability privately.
