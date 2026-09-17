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
| Listens on `127.0.0.1` only (`server.host` accepts only `127.0.0.1` in this version) | The network |
| **Host allowlist**: answers only for `127.0.0.1:<port>` and `localhost:<port>` | DNS rebinding |
| **Exact Origin allowlist** on state-changing requests and every WebSocket: its own origin and `security.allowed_origins`, compared by scheme, host and port | Cross-site requests and WebSocket hijacking |
| **CORS** headers only for listed origins, never `*` | Pages reading its answers |
| **`frame-ancestors`**: itself and `security.frame_ancestors` | Clickjacking |
| `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` | Content sniffing; codes leaving in a referrer |

A request with no `Origin` is not from a page -- it is a CLI or a backend -- and its token decides it. The rules are read
on every request, so a changed setting applies at once.

**The CLI checks it is talking to your daemon.** A port on `127.0.0.1` is anyone's to listen on -- another account on
the Mac, or a program started first. So before `sim-mirror` sends the admin token or an agent token anywhere, it asks
`/healthz` with a fresh nonce and no credential, and goes on only when the answer proves the listener holds the admin
token (an HMAC-SHA256 of the nonce keyed by it). Something else on the port is refused and sent nothing.

## Credentials

| Credential | Is | Lives |
|---|---|---|
| **Admin token** | Made on the daemon's first start; may do everything | A file readable only by you, in Application Support |
| **Scoped tokens** (`agent`, `viewer`, `admin`, `host`) | For a kind of client and a list of scopes, namespaces (`notes:*`) or `*`; an agent or host token may name folders; a token a host made names that host | Only their SHA-256, in `tokens.json` (0600); shown once when made; compared in constant time |
| **Login codes and embed tickets** | One-shot, 60 seconds, for one scope | In a URL **fragment**, which browsers never send to a server; spent at `/api/v1/auth/exchange` for a viewer token |
| **Viewer sessions** | A viewer token from a code, 12 hours; one from `sim-mirror open --settings` may change settings, for 1 hour | In the page's memory and the daemon's; never on disk |
| **Settings confirmation codes** | One-shot, 5 minutes, bound to one waiting settings change, dropped after 5 wrong tries | In the daemon's memory; shown only to the admin token, by `sim-mirror settings confirm` |
| **Screen socket tickets** | One-shot, 60 seconds, for one scope and -- when minted for a page -- that page's origin | Minted only by an authenticated start |

- An agent's routes take only an agent token, for the scope it names; a person's routes take the admin token or a
  viewer credential for the scope.
- `sim-mirror mcp` passes its agent token to the relay in the environment, never argv, and revokes it when the client
  goes.
- Tokens and tickets in a query string are redacted from the server's logs.
- A viewer credential for a scope may choose any simulator on the Mac from the device picker, including one another
  scope uses: the picker is the person's whole Mac. A viewer session spent from an embed ticket lasts its 12 hours even
  if the token that minted the ticket is revoked.

## Settings from a page

The viewer's settings panel changes `config.toml`, which decides what SimMirror runs and who may reach it -- so a
page, which is untrusted, gets as little as does the job:

| Caller | Reads settings | Changes settings | Changes a sensitive setting |
|---|---|---|---|
| A session from `sim-mirror open --settings` | yes | yes | only with a code from the terminal |
| A session from `sim-mirror open`, or a viewer token | yes | no | no |
| A framed viewer from an embed ticket, or an agent | no | no | no |
| The admin token from outside a page (the CLI) | yes | yes | yes |

- **Only from the daemon's own pages.** A request with any other `Origin` is refused, including one
  `security.allowed_origins` lets call the API -- such an origin gets CORS answers on every route -- as is a browser's
  cross-site `Sec-Fetch-Site`. `PATCH` is also not among the methods CORS allows.
- **Sensitive settings wait for a person.** `connectors.idb.companion_path`, `device.developer_dir`, `build.tools`,
  `server.*` and `security.*` decide what runs or who may reach the daemon. A page's change to one is held until
  `sim-mirror settings confirm` -- with the admin token, which no page holds -- shows it as it would be written and
  gives a code; the code confirms that exact change, once. A page cannot learn a code, so a script that got into a
  settings session still cannot turn on commands or widen the origins.
- **A value a variable or the command line sets is not written**: the change would not take effect, so it is refused
  rather than silently ignored.
- A host that mounts `create_settings_router` decides all of this through its own `SettingsAuthenticator`; one that
  mounts nothing exposes no settings to a page.

## Hosts sharing the daemon

A **host token** is for an application sharing the daemon, made by the admin for one or more namespaces that no other
host has. With it, the application:

- reaches the person routes, embed tickets and settings of scopes in its namespaces only -- settings one scope at a
  time, and a sensitive one only once a person confirms it at the terminal;
- makes, lists and revokes `agent` and `viewer` tokens for those scopes, naming only folders inside its own; never an
  `admin` or `host` token, and never a token another host made;
- never reaches the admin routes, an agent's routes, or a scope outside its namespaces.

**Devices stay apart**: a scope joins or picks a simulator only when every scope running it belongs to the same host
-- or all of them to the Mac -- and a picker leaves out the simulators another host's scopes are running. What is still
shared: the daemon's booted-device limit, and a simulator's name, which says the scope it was made for, in the list of a
host whose scopes are not running it.

**Revoking a host revokes every token it made**, at once. Viewer sessions already spent from its embed tickets last
their 12 hours, like any other.

**A host checks the daemon before sending its token**: `/healthz` answers a host's nonce with a proof keyed by the
SHA-256 of its token, which only the daemon -- keeping that digest -- and the host itself can make.

## What an agent can reach

- **Its scope's device only**, through the tool whitelist; the screen socket accepts only the protocol's input
  messages. The one exception is where tests run: `sim_test`'s `destination` may send them to another available iOS
  simulator on this Mac for the scope's Xcode -- never one another scope is running, one another scope's tests are
  running on, or one another process on the Mac has claimed -- and SimMirror neither shows nor drives it.
- **URLs**: `sim_app open_url` refuses `file:`, `data:`, `javascript:` and `about:` URLs and the device's settings
  URLs.
- **Installs**: a built `.app` inside a folder its token names, the scope's DerivedData or Xcode's -- with every link
  followed before the check.
- **Commands**: builds and tests run `xcodebuild` only while `build.tools` is on (off by default), in the folder its
  token names, with a timeout, one at a time per scope. Scheme, configuration and test plan names reach it as single
  arguments, never through a shell, and a name that starts with `-` is refused, so none can become an option.
- **Nothing outside the device**: every gesture is drawn by the viewer's own cursor, never the Mac's pointer.

## Xcode's tools

SimMirror runs `xcrun mcpbridge` only for a scope that asks -- `connectors.preferred = "mcpbridge"` or
`connectors.mcpbridge.merge` -- and `sim-mirror xcode approve` when you run it. Through it, it calls three of Xcode's
tools: it opens a device-interaction session, captures the screen with no command, and ends the session. It never sends
a tap or a key through them. The approval Xcode gives is yours to give: nothing asks for it but that command, which
opens only the project you name, or the one in the folder you run it in.

A capture writes the hierarchy, a screenshot, a thumbnail and a log to Xcode's temporary folder. SimMirror reads the
hierarchy and removes those files -- only ones in the folder the hierarchy was written to and named for its own
session. The hierarchy's text is the app's: a label worded to look like more of the hierarchy can misname that one
element, as the app could by showing it.

## Apps that share their hierarchy

An app built with [SimMirrorKit](app-sdk.md) answers SimMirror with its own view hierarchy, only in a Debug build on the
simulator; a Release build holds none of it (`make sdk-release-check`). The app listens on 127.0.0.1 only, with a new
random secret each launch, which it writes with its port to a listing in the simulator's data folder readable by the Mac
user alone (0600). It checks the secret before anything else, never logs it, and refuses a request from a web page (an
`Origin` header) or addressed to another host, so a page in a browser cannot read it even by guessing the port.

SimMirror reads a listing only when it is a regular file, not a link, owned by the Mac user and readable by nobody else,
at most 16 KB, naming this device and a process still running; it never deletes one. It sends the secret only in a
request's `Authorization` header, never in a URL or a log, reads at most 8 MB of an answer within
`connectors.app.timeout_ms`, and treats what it reads as the app's words: a label worded to look like more of the
hierarchy can misname that one element, as the app could by showing it. What an app shares is what an agent driving the
simulator could see on its screen.

## The relay

The MCP relay is one standard-library file. It reads its URL and credentials from the environment, talks only to
loopback addresses, and ignores proxy settings, so a tool call never leaves the machine.

## What leaves your Mac

Nothing, from SimMirror itself. What an agent sees -- snapshots, screenshots, logs -- goes to the agent's model provider
as the agent's client sends it.

## Reporting

See [SECURITY.md](../SECURITY.md) for how to report a vulnerability privately.
