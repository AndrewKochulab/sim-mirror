# What SimMirror promises not to break

SimMirror is used in ways that outlive a single version: an agent calls its **tools**, a page speaks its **protocol**
and embeds its **viewer**, a host application embeds its **Python API** or shares its daemon, and a script runs its
**command line**. Each of those is a promise to somebody who is not in this repository, so each is written down here —
what is covered, what is not, and what happens when one has to change.

This page is the policy. The surface check holds the code to it, and [the changelog](../CHANGELOG.md) says what each
version added.

## Versions

SimMirror has two version numbers, and they mean different things.

| Number | Where | What it counts |
|---|---|---|
| Package version | `sim-mirror` on PyPI, `@andrewkochulab/sim-mirror` on npm, the git tag | The code: [semantic versioning](https://semver.org/spec/v2.0.0.html) |
| Protocol version | `PROTOCOL_VERSION`, the `v` in every hello | The wire between a viewer and a server |

They move independently on purpose: a package release that changes nothing a viewer can see leaves the protocol
where it is, and a viewer built against protocol `v1` keeps working across those releases.

## Before 1.0

`0.x` promised nothing: it was the period for getting these shapes right while there were few enough users that fixing
one was cheaper than living with it, and 0.1.1 and 0.2.0 did change some. If you are still on a `0.x` release, read the
changelog up to 1.0.0 before moving.

## From 1.0

Six surfaces are stable, and semantic versioning means what it says: nothing below breaks in a `1.x` release.

**It is checked, not only written.** [`compat/surface-v1.json`](../compat/surface-v1.json) records the whole surface as
1.0 has it -- names, parameters, fields, schemas, constants, close codes, tool arguments, settings, commands, flags and
the viewer package's exports -- and `scripts/surface.py`, run by the tests, fails when the code no longer keeps any of
it. An addition passes; a break is named. The file is written once for version 1 and never regenerated to let a break
through: a break is version 2.

### The agent tools

The tool **names** (`sim_device`, `sim_snapshot`, `sim_screenshot`, `sim_act`, `sim_app`, `sim_build_run` and
`sim_test`), their **arguments**, the kinds of `sim_act` step and what each takes, and the **shape** of what they
answer.

An agent reads results as text, so the exact words are *not* part of the promise -- they are tuned as models change,
and a summary getting clearer is not a break. What is promised is that a call that worked keeps working: an argument is
not removed, made required or narrowed, and a tool does not quietly start doing something else.

Tools appear and disappear with a device's **capabilities** -- a view-only connector offers no `sim_act` -- and that
is the documented behaviour, not a break.

### The protocol

Everything in [`protocol/v1/`](../protocol/README.md): the screen socket's messages, the binary frame tags, the close
codes and the constants, and what the HTTP routes answer -- a scope's status, devices and settings, embed tickets, the
health check, a host's tokens, and the agent routes' manifest and results. The types and constants
`sim_mirror.protocol` and the viewer package generate from it are covered with it. Within `v1`:

- a field is **never removed** and never changes meaning or type, and a field that is always sent stays always sent;
- a new field is **safe to ignore** -- every receiver already ignores what it does not know -- and a server never needs
  a client to send one it did not send before;
- a value a server accepts is never refused later: no enum member, type or length it took is taken away;
- a new enum member (a capability, an encoding, a kind of token) may appear, so read enums as open. A client says what
  it can handle in its hello and gets only that.

A change that cannot be made this way gets `v2`, a folder beside `v1`, and a server that speaks both while `v1` is
supported. A server that cannot speak a client's version closes the socket with `4406`, which is why the version is
in the hello rather than assumed.

The routes a host's backend uses -- a scope's, a host's own, and the agent routes -- are stable at their paths. The
admin routes (`/api/v1/admin/…`) are the command line's, and the command line is their stable interface.

### The Python embedding API

Every name exported by [`sim_mirror.api`](../src/sim_mirror/api.py) -- the `Runtime`, the seams a host implements,
the router factories, `relay_command`, the settings router and its seams, `DaemonHost` for a host sharing the daemon,
and the helpers beside them. `tests/unit/test_api.py` writes that list down, and the surface check holds each name to
what it was:

- **What a host calls** -- a function, a constructor, a method -- keeps every parameter, in its position and by its
  name. A new parameter has a default; a parameter that had one keeps it.
- **What a host implements** -- `ConfigSource`, `StateStore`, `DeviceMemory`, `UsageProbe`, `Policy`, `Authenticator`,
  `SettingsStore`, `SettingsAuthenticator`, `Confirmations` -- never gains a method or changes how SimMirror calls one.
  Something new a host may answer is a new seam beside it, optional to pass.
- **What a host reads** -- `Person`, `Caller`, `Scope`, `SettingsEditor`, `AgentAccess`, a refusal's `status` and
  `message`, every attribute of `SimConfig` -- keeps each field.

`Runtime` is made by `Runtime.build`, never from its fields, and what is promised of it is what a host does: `build`
with `config`, `state`, `policy`, `memory`, `copy`, `usage` and `may_share`; then `start`, `reconcile`, `refusal`,
`manifest`, `call` and `close`. `build`'s other parameters (`registry`, `claims`, `tools`, `builds`, `xcrun`,
`keyboard_is_us`, `hierarchy`, `clock`, `sleep`, `platform`) and `Runtime`'s fields are how SimMirror's own tests put
one together, and are not. `SimConfig` is made by `SimConfig.defaults`, `from_flat`, `overlay` and `with_values`.

**Nothing else in the package is public.** `sim_mirror.core`, `sim_mirror.connectors`, `sim_mirror.daemon` and the
rest are the implementation and change freely. If you need something that is not on `api`, that is a hole in the
surface and worth [an issue](https://github.com/AndrewKochulab/sim-mirror/issues) -- reaching in instead is how an
upgrade nobody called breaking breaks your application.

`sim_mirror.testing` is offered to hosts for their own tests and follows the same rule as the API, one release
behind: it may change in a minor release when the thing it fakes does.

### The viewer package

What `@andrewkochulab/sim-mirror` exports -- `createViewer`, `createHttpTransport`, `TransportError`, the
`<sim-mirror>` element and `defineSimMirrorElement`, the icons, `VIEWER_CSS` and the protocol's types -- the element's
attributes (`server`, `scope`, `token`, `placement`), its `sim-mirror:state`, `sim-mirror:place` and
`sim-mirror:close` events, the `bar`, `stage` and `screen` parts, and the `--sim-mirror-*` custom properties.

`ViewerOptions` and `HttpTransportOptions` never gain a member a page has to give; `SimMirrorTransport`, which a host
with its own API client implements, never gains one it has to write; and `ViewHandle` and `ViewerState` keep each
member a page reads.

### Settings and the command line

**Settings** keep their paths (`stream.fps`), their environment variables (`SIM_MIRROR_STREAM_FPS`), whether they can
be set for one scope, and every value they accepted: a choice keeps its options, a number its range, a text its length.
A setting may take more; a **default** may be re-tuned (see below).

**The command line** keeps its commands, their flags and arguments, and their exit codes. A new flag or argument is
optional.

### What is not covered

- **The words.** A tool's text, a refusal's message, the CLI's output: tuned freely. Use `--json` where a program needs
  to read a result, and a status code or a field rather than a message.
- **Configuration defaults.** A default may be re-tuned in a minor release -- frame rates, timeouts, how long a device
  sits idle. Set what you depend on.
- **The viewer's DOM and CSS class names.** Style it through the custom properties and `::part()`; the markup inside,
  the settings panel's included, is not stable.
- **Connectors.** The `sim_mirror.connectors` entry point and the `Connector` protocol are not stable in 1.x: a native
  helper, real devices and Android will reshape them. A connector outside this repository pins a minor version.
- **What an agent sees of a screen.** Which elements a snapshot holds, how they are named, and what reading Xcode's UI
  hierarchy adds, improve as the readers do.
- **The app SDK.** SimMirrorKit's Swift API and its wire format, `protocol/app-sdk/v1`, are a preview in 1.x with a
  version of their own, outside `compat/surface-v1.json`: a change that would break an app speaking version 1 gets
  version 2, which SimMirror reads beside it, and an app's SDK says which it speaks.
- **The `build_preview` capability.** It stays in protocol `v1`, reserved: no connector offers it, and no server sends
  it.

## Deprecation

Nothing stable is removed in a minor release. When something has to go:

1. It is **deprecated** in a minor release: still working, warning where a warning can be seen, and the changelog says
   what to use instead.
2. It keeps working for **at least one further minor release**, so there is a version where both the old and the new
   way work and you can move without a flag day.
3. It is removed in the next **major** release, listed under **Removed**.

Security is the exception. If keeping something working would keep a vulnerability open, it changes as fast as the
fix needs, and [SECURITY.md](../SECURITY.md) says how that is communicated.

## Supported versions

The latest minor release gets fixes. The previous minor release gets security fixes for three months after its
successor. `0.x` releases are no longer supported once 1.0.0 is out.
