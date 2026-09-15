# Installation

## Requirements

| | Needed for |
|---|---|
| A Mac with Apple Silicon and a logged-in desktop session | Simulators; they do not show over SSH |
| Xcode 26 or later, with an iOS runtime | Everything (`xcode-select -p` names the one used) |
| [uv](https://docs.astral.sh/uv/) | Installing and running SimMirror (Python 3.10 or later) |
| idb_companion 1.5 | Touching the screen and reading its element tree; without it the viewer is view-only |

[Compatibility](compatibility.md) has the details.

## Install SimMirror

As a command on your `PATH` (recommended):

```sh
uv tool install git+https://github.com/AndrewKochulab/sim-mirror@v0.1.0
```

Or run it without installing, as MCP client configurations do:

```sh
uvx --from git+https://github.com/AndrewKochulab/sim-mirror@v0.1.0 sim-mirror doctor
```

Or as a library in a Python project that embeds it (see [Python host applications](embedding/python-fastapi.md)):

```sh
uv add "sim-mirror @ git+https://github.com/AndrewKochulab/sim-mirror@v0.1.0"
```

The viewer's page is built into the package, so none of these needs Node. Packages on PyPI and a Homebrew formula come
with v1.0.

## Install idb_companion

```sh
brew install facebook/fb/idb-companion
```

SimMirror finds it on `PATH` or where Homebrew puts it; set `connectors.idb.companion_path` for anywhere else. Check
with `sim-mirror doctor`.

## The viewer library, for your own pages

The npm package `@andrewkochulab/sim-mirror` is attached to each GitHub Release until it is published to npm:

```sh
npm install https://github.com/AndrewKochulab/sim-mirror/releases/download/v0.1.0/andrewkochulab-sim-mirror-0.1.0.tgz
```

See [the web component](embedding/web-component.md).

## Where files live

| Folder | Holds | Moved by |
|---|---|---|
| `~/Library/Application Support/SimMirror` | `config.toml`, the admin token, `tokens.json` (digests only), remembered devices, build results | `SIM_MIRROR_STATE_DIR` |
| `~/Library/Application Support/SimMirror/claims` | Which process uses which device, shared by every SimMirror on the Mac | `SIM_MIRROR_CLAIMS_DIR` |
| `~/.sim-mirror/run` | `daemon.json`, idb_companion sockets and pid files (kept short: a socket path may have 104 bytes) | `SIM_MIRROR_RUN_DIR` |
| `~/Library/Logs/SimMirror` | The daemon's and the companions' logs | `SIM_MIRROR_LOG_DIR` |

`SIM_MIRROR_CONFIG` points at a different `config.toml`. Every folder is private to your user.

## Update

```sh
uv tool install --force git+https://github.com/AndrewKochulab/sim-mirror@v<new version>
```

and change the tag in your MCP client configurations. A running daemon keeps the old version until it is restarted.

## Uninstall

```sh
uv tool uninstall sim-mirror
rm -rf ~/Library/Application\ Support/SimMirror ~/.sim-mirror ~/Library/Logs/SimMirror
```

Devices SimMirror created are named `SimMirror · …` in Xcode's device list; delete them there if you no longer want
them.
