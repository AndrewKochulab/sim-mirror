# Troubleshooting

Start with `sim-mirror doctor`: most problems show up there with a fix. Logs are in `~/Library/Logs/SimMirror`.

## Taps do nothing

The screen shows, snapshots read it, but taps, typing and buttons have no effect -- and `sim-mirror doctor` says
"Input was swallowed".

On Xcode 27, a simulator booted while **Device Hub** is open takes input only through Device Hub's own transport and
silently ignores idb_companion's. Close Device Hub, shut the device down, and boot it again with Device Hub closed
(SimMirror boots it headless when it starts it). Synthetic scrolling can also be dropped there; agents use drags.

## The viewer is view-only

The viewer says it is a mirror, and agents are told `sim_act` needs a connector that can touch the screen.

SimMirror fell back to the `simctl` connector because idb_companion was not found. Install it
(`brew install facebook/fb/idb-companion`), or point `connectors.idb.companion_path` at it, and check with
`sim-mirror doctor`. With `connectors.preferred = "idb"` SimMirror refuses instead of falling back, and says why.

## The simulator does not boot or show

Simulators need a logged-in desktop session. Over SSH, from a launch daemon, or before anyone has logged in, they may
not boot. `sim-mirror doctor` reports the session. Check that an iOS runtime is installed (the `runtimes` check).

## "Another process is already using this simulator"

Two SimMirrors -- the daemon and an application embedding SimMirror, say -- never drive one device at once. The message
names the other process. Stop it, or give this scope another device: `sim-mirror devices list`, then
`sim-mirror devices choose UDID`. A claim held by a process that has exited is taken over automatically.

## A socket path is too long

idb_companion serves on a unix socket, whose path may have at most 104 bytes. SimMirror keeps them in `~/.sim-mirror/run`
for that reason; if you moved it with `SIM_MIRROR_RUN_DIR`, choose a shorter folder.

## Leftover idb_companion processes

Each companion is ended when its device is let go. One a crashed SimMirror left behind is ended the next time that
SimMirror starts -- only if the process that started it is gone, and never one another host started.

## The viewer says the simulator is off

`enabled` is `false` for this scope (`sim-mirror config get enabled`), and the screen socket was closed with 4403.
Switch it on with `sim-mirror config set enabled true`.

## An iframe stays blank

The page's origin is not in `security.frame_ancestors`; the browser's console says so. See
[the iframe guide](embedding/iframe.md).

## The web component shows nothing

The page's origin is not in `security.allowed_origins`, so the daemon sends it no CORS headers and closes its screen
socket (4403). See [the web component guide](embedding/web-component.md).

## Only JPEG, never H.264

H.264 is decoded with WebCodecs, which needs a secure page -- HTTPS, or `http://127.0.0.1`/`localhost` -- and a browser
that decodes H.264. Anywhere else the viewer uses JPEG. `stream.encoding` can force either.

## Agent tools are missing

- `agent.tools` is `false` for the scope.
- The connector cannot serve them: a view-only mirror offers no `sim_act` or `sim_snapshot` (see above).
- `sim_build_run` and `sim_test` are listed only while `build.tools` is on.

`sim-mirror tools` lists what this install offers.

## An agent's device stopped between calls

A device nobody watches, no agent holds and nothing builds on is stopped after `device.idle_minutes` (15). `sim-mirror
mcp` holds its scope's device while the client runs; a client that exited or crashed lets it go. Raise
`device.idle_minutes`, or set `device.shutdown_on_idle` to `false` to leave idle devices booted.

## Still stuck

Open a [bug report](https://github.com/AndrewKochulab/sim-mirror/issues/new?template=bug.yml) with
`sim-mirror doctor --json` and the relevant lines from the log.
