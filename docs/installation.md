# Installation

## Requirements

| | Needed for |
|---|---|
| A Mac with Apple Silicon and a logged-in desktop session | Simulators; they do not show over SSH |
| Xcode 26 or later, with an iOS runtime | Everything (`xcode-select -p` names the one used) |
| [uv](https://docs.astral.sh/uv/) or Homebrew | Installing and running SimMirror (Python 3.10 or later; Homebrew brings its own) |
| idb_companion 1.5, optional | Another way to touch the screen and read it; SimMirror's own helper needs no install |

[Compatibility](compatibility.md) has the details.

## Install SimMirror

As a command on your `PATH` (recommended):

```sh
uv tool install sim-mirror==1.2.0
```

Or with [Homebrew](https://github.com/AndrewKochulab/homebrew-tap):

```sh
brew install andrewkochulab/tap/sim-mirror
```

The formula builds SimMirror from its PyPI release in its own Python 3.13 environment, so the first install compiles a
few Python extensions and takes a few minutes. The tap picks up a new release once it has been on PyPI for a day;
`brew upgrade sim-mirror` then brings it. An MCP client can run this install's command in place of `uvx`:
`claude mcp add sim-mirror -- sim-mirror mcp`.

Or run it without installing, as MCP client configurations do:

```sh
uvx --from sim-mirror==1.2.0 sim-mirror doctor
```

Or as a library in a Python project that embeds it (see [Python host applications](embedding/python-fastapi.md)):

```sh
uv add "sim-mirror==1.2.0"
```

SimMirror is [on PyPI](https://pypi.org/project/sim-mirror/). The viewer's page is built into the package, so none of
these needs Node.

## The native helper

SimMirror touches, reads and streams a simulator's screen with its own helper, `sim-mirror-helper`
([Connectors](connectors.md#native)). The wheel on PyPI carries it, built for both Mac architectures, so `uv tool
install` and `uvx` need nothing more. An install without it -- Homebrew's, which builds from source, or a git checkout
-- builds it once with the Xcode it uses:

```sh
sim-mirror helper build     # a minute or two; kept in the state folder, under helpers/
sim-mirror helper status    # which helper SimMirror runs, and whether it fits this SimMirror
```

Until then `sim-mirror doctor` says so, and `auto` uses idb_companion if it is there, or simctl, view-only.

## idb_companion, optionally

```sh
brew install facebook/fb/idb-companion
```

SimMirror finds it on `PATH` or where Homebrew puts it; set `connectors.idb.companion_path` for anywhere else. `auto`
uses it when the native helper cannot be used, and `connectors.preferred = "idb"` always. Check with
`sim-mirror doctor`.

## The viewer library, for your own pages

The viewer is [on npm](https://www.npmjs.com/package/@andrewkochulab/sim-mirror) as `@andrewkochulab/sim-mirror`:

```sh
npm install @andrewkochulab/sim-mirror@1.2.0
```

See [the web component](embedding/web-component.md).

## Where files live

| Folder | Holds | Moved by |
|---|---|---|
| `~/Library/Application Support/SimMirror` | `config.toml`, the admin token, `tokens.json` (digests only), remembered devices, build results, the helpers built here (`helpers/`) | `SIM_MIRROR_STATE_DIR` |
| `~/Library/Application Support/SimMirror/claims` | Which process uses which device, shared by every SimMirror on the Mac | `SIM_MIRROR_CLAIMS_DIR` |
| `~/.sim-mirror/run` | `daemon.json`, the native helpers' and idb_companion's sockets and pid files (kept short: a socket path may have 104 bytes) | `SIM_MIRROR_RUN_DIR` |
| `~/Library/Logs/SimMirror` | The daemon's, the native helpers' and the companions' logs | `SIM_MIRROR_LOG_DIR` |

`SIM_MIRROR_CONFIG` points at a different `config.toml`. Every folder is private to your user.

## Update

```sh
uv tool upgrade sim-mirror
```

and change the version in your MCP client configurations (`sim-mirror==<new version>`). A running daemon keeps the old
version until it is restarted.

## Uninstall

```sh
uv tool uninstall sim-mirror
rm -rf ~/Library/Application\ Support/SimMirror ~/.sim-mirror ~/Library/Logs/SimMirror
```

Devices SimMirror created are named `SimMirror · …` in Xcode's device list; delete them there if you no longer want
them.
