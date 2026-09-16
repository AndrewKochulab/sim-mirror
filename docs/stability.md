# What SimMirror promises not to break

SimMirror is used in three ways that outlive a single version: an agent calls its **tools**, a page speaks its
**protocol**, and a host application embeds its **Python API**. Each of those is a promise to somebody who is not in
this repository, so each is written down here — what is covered, what is not, and what happens when one has to change.

This page is the policy. Whether a given version keeps it is [the changelog](../CHANGELOG.md)'s job to say.

## Versions

SimMirror has two version numbers, and they mean different things.

| Number | Where | What it counts |
|---|---|---|
| Package version | `sim-mirror 0.1.0`, the npm viewer, the git tag | The code: [semantic versioning](https://semver.org/spec/v2.0.0.html) |
| Protocol version | `PROTOCOL_VERSION`, the `v` in every hello | The wire between a viewer and a server |

They move independently on purpose: a package release that changes nothing a viewer can see leaves the protocol
where it is, and a viewer built against protocol `v1` keeps working across those releases.

## Before 1.0

**Anything here may change in a minor release, and some of it has.** `0.x` is the period for getting these shapes
right while there are few enough users that fixing one is cheaper than living with it. Every such change is called
out under **Changed** in the changelog, with why — see 0.1.1, which moved a seam that every host had to implement
and almost none used.

If you are embedding SimMirror now, pin an exact version and read the changelog before moving.

## From 1.0

Three surfaces become stable, and semantic versioning starts meaning what it says.

### The agent tools

The tool **names** (`sim_device`, `sim_snapshot`, `sim_screenshot`, `sim_act`, `sim_app`, `sim_build_run` and
`sim_test`), their **arguments**, and the **shape** of what they answer.

An agent reads results as text, so the exact words are *not* part of the promise — they are tuned as models change,
and a summary getting clearer is not a break. What is promised is that a call that worked keeps working, that an
argument is not removed or made required, and that a tool does not quietly start doing something else.

Tools appear and disappear with a device's **capabilities** — a view-only connector offers no `sim_act` — and that
is the documented behaviour, not a break.

### The protocol

Everything in [`protocol/v1/`](../protocol/README.md): the messages, the binary frame tags, the close codes and the
constants. Within `v1`:

- a field is **never removed** and never changes meaning or type;
- a new field is **optional to send and safe to ignore** — every receiver already ignores what it does not know;
- a new enum member (a capability, an encoding) may appear, so read enums as open. A client says what it can handle
  in its hello and gets only that.

A change that cannot be made this way gets `v2`, a folder beside `v1`, and a server that speaks both while `v1` is
supported. A server that cannot speak a client's version closes the socket with `4406`, which is why the version is
in the hello rather than assumed.

### The Python embedding API

Every name exported by [`sim_mirror.api`](../src/sim_mirror/api.py) — the `Runtime`, the seams a host implements,
the router factories, `relay_command` and the helpers beside them. `tests/unit/test_api.py` writes that list down,
so a name cannot leave it by accident.

**Nothing else in the package is public.** `sim_mirror.core`, `sim_mirror.connectors`, `sim_mirror.daemon` and the
rest are the implementation and change freely. If you need something that is not on `api`, that is a hole in the
surface and worth [an issue](https://github.com/AndrewKochulab/sim-mirror/issues) — reaching in instead is how an
upgrade nobody called breaking breaks your application.

`sim_mirror.testing` is offered to hosts for their own tests and follows the same rule as the API, one release
behind: it may change in a minor release when the thing it fakes does.

### What is not covered

- **The CLI's output text.** The commands, their flags and their exit codes are stable; the words they print are not.
  Use `--json` where a program needs to read a result.
- **Configuration defaults.** A default may be re-tuned in a minor release — frame rates, timeouts, how long a device
  sits idle. Set what you depend on.
- **The viewer's DOM and CSS class names.** Style it through the documented custom properties and `::part()`, which
  are stable; the markup inside is not.
- **Anything marked preview.** Build and test left preview in 0.2.0. The `build_preview`
  capability stays in protocol `v1`, reserved: no connector offers it, and no server sends it.
- **Hosts sharing the daemon, until 1.0.** Host tokens and namespaces, `/api/v1/host`, the `token_proof` of
  `/healthz`, and `DaemonHost`, `AgentAccess`, `DaemonRefused` and `DaemonUnavailable` on `sim_mirror.api` are new in
  0.2.0 and may still change shape before 1.0.
- **The settings panel, its routes and seams, until 1.0.** `settings.schema.json`, `create_settings_router`,
  `SettingsStore`, `SettingsAuthenticator`, `SettingsEditor`, `SettingsRefused` and `Confirmations` are new in
  0.2.0 and may still change shape before 1.0; the panel's markup is the viewer's, and never stable.

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

The latest minor release gets fixes. Once 1.0 is out, the previous minor gets security fixes for three months after
its successor. Before 1.0, only the latest release is supported.
