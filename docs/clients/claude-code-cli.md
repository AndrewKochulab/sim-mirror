# Claude Code in a terminal

Claude Code drives the simulator through SimMirror's MCP tools; you watch, and step in, in a browser tab.

## Install with the plugin

```
/plugin marketplace add AndrewKochulab/sim-mirror
/plugin install sim-mirror@sim-mirror
```

The plugin brings:

- the tools, from `sim-mirror mcp` run with uvx at the release's tag;
- a **skill** that teaches the snapshot-first loop -- read with `sim_snapshot`, act with `sim_act` batches and waits,
  screenshot only for what text cannot tell;
- **`/sim-mirror:open`**, which opens this project's viewer.

Its tools are named `mcp__plugin_sim-mirror_sim-mirror__sim_snapshot` and so on. To try a local copy of the plugin
instead, start Claude Code with `claude --plugin-dir plugins/sim-mirror`.

## Install without the plugin

```sh
claude mcp add sim-mirror -- uvx --from sim-mirror==2.1.0 sim-mirror mcp
```

`--scope project` (before the name) writes it to the project's `.mcp.json`, for everyone working on it. The tools are
named `mcp__sim-mirror__sim_snapshot` and so on.

## Which project

`sim-mirror mcp` gives the agent **the scope of the folder Claude Code runs in** -- its own simulator -- and that
folder as the only place `sim_app install` and the build tools may reach. To choose otherwise:

```sh
claude mcp add sim-mirror -- uvx --from sim-mirror==2.1.0 \
  sim-mirror mcp --scope my-app --root /Users/me/Projects/my-app
```

### Git worktrees

An agent often works in a git worktree of the project -- a second checkout, on another branch -- after the session
started in the main checkout. `sim_build_run` and `sim_test` build the folder the agent works in, not the one the
server started in:

1. the folder a call names in `folder`;
2. else the folder the client says it works in (its MCP roots), when the client offers them;
3. else the folder `sim-mirror mcp` started in, or its first `--root`.

Claude Code's `EnterWorktree` moves its MCP roots into the worktree, but sends no `notifications/roots/list_changed`
when it does (measured with Claude Code 2.1.301), so the relay asks the client for its roots again before every tool
call: one local round trip, which Claude Code answers at once. A client that does not answer within two seconds has
the call go with the roots it gave last, and is not asked before a call again. A `cd` in the shell moves nothing: the
roots stay where the session is, so an agent that changed folders that way names the worktree in `folder`.

A build may run in the `--root` folder or a folder inside it, or in the same folder of **another checkout of its
repository** -- its main checkout or a linked worktree, wherever it is. With `--root ~/app/ios`, the worktree
`~/app-feature` builds in `~/app-feature/ios`. A checkout is known from its `.git` files, and one is a worktree only
when its repository names it back, so no other folder can be built by claiming to be one. When the client works
somewhere else, the build answers with a note saying which folder it built instead.

A main checkout made with `--separate-git-dir` keeps its git folder elsewhere and holds only a `.git` file naming it.
Its worktrees build like any others when the `--root` folder is in that main checkout. The git folder names no
checkout back, though -- git itself cannot find that main checkout from a worktree, and `git worktree list` names the
git folder in its place -- so a copy of its `.git` file would look the same. It is trusted as the `--root` folder's
own, or when its git folder names it back. To let a session rooted in one of its worktrees build the main checkout
too, run this once at the top of the main checkout:

```sh
git config core.worktree "$PWD"
```

That writes the main checkout's folder into the git folder's `config`, a link back as a worktree's is, and git itself
goes by it (a worktree's git leaves it out of account). Move the main checkout and run it again. Without it, the main
checkout is not a place builds from such a session may go.

The device and its scope stay the session's, and so do its build logs and its DerivedData: every checkout the session
builds shares one DerivedData folder, the scope's. Switching between worktrees costs a rebuild of what differs, but a
folder per worktree would cost more than it saves. An iOS app's DerivedData is gigabytes -- its products, index, module
cache and Swift packages -- and agents make worktrees freely, one per task. Each new one would resolve and build every
package from nothing, and SimMirror would have to delete multi-gigabyte folders behind a person's back to keep the disk
from filling. One folder per scope keeps the packages and module cache warm, and since a scope runs one build at a
time, sharing it is safe. When a stale product is suspected, delete the folder
(`~/Library/Developer/Xcode/DerivedData/SimMirror-<scope>`); the next build makes it again.

A build in a worktree runs with the settings of the folder it builds when that folder's project has settings of its
own -- its `[scopes."<id>"]` table, as the settings panel of `sim-mirror open` run there writes it -- and with the
session's otherwise.

A session started **in** a worktree is that worktree's scope, which has no settings of its own until someone gives it
some. `sim-mirror doctor`, run in the worktree, says when its main checkout's scope has settings this one lacks, and
gives the command that copies them -- for a `--separate-git-dir` main checkout, once its git folder names it back as
above; it changes nothing itself:

```sh
sim-mirror config copy --from project-app-1a2b3c4d --scope project-app-feature-5e6f7a8b
```

A value the worktree's scope sets already is kept.

### Is the app on the device the code under review?

Every build answer ends with the `source` it built -- folder, branch, commit, `(dirty)` -- but the app on the device
says nothing of where it came from. With `build.stamp_source` on, `sim_build_run` installs on a simulator a copy of
the app whose `Info.plist` also holds `SIM_MIRROR_SOURCE_SHA` (the commit) and `SIM_MIRROR_SOURCE_DIRTY`, and
`sim_app` `info` reads them back and compares them with the folder now:

```text
com.example.notes · Notes 1.2 (7) · /Users/me/Library/Developer/CoreSimulator/Devices/…/Notes.app
stamped 1a2b3c4 (SIM_MIRROR_SOURCE_SHA)
the code in /Users/me/app-feature/ios: 1a2b3c4, with nothing uncommitted then or now
```

The project needs no change, and the app in DerivedData is left as Xcode built it: no build setting can add a key to
an `Info.plist` the project does not name, so the key goes into a copy -- a copy-on-write clone, made in an instant --
that is signed again ad hoc, as Xcode signs every simulator app, installed, and removed. A real device's app is signed
for it by your team, which SimMirror does not re-sign, so it is never stamped; nor is a build whose folder git names no
commit for. Either way the app is installed as built, and the answer says why it carries no stamp.

## Permissions

Claude Code asks before each new tool. To allow them all, add the server's rule to your permission settings:
`mcp__sim-mirror` (or `mcp__plugin_sim-mirror_sim-mirror` for the plugin).

## Watch it

```sh
sim-mirror open                  # or /sim-mirror:open with the plugin
```

The viewer shows the device live; every agent gesture is drawn as a cursor just before it lands
(`agent.cursor_lead_ms`, 250 ms by default, only while someone watches). You can use the device yourself at any time: an
agent waits for your hand to be still before its next gesture.

## A first prompt

> Open Settings → General → About and tell me which iOS version this simulator runs. Use sim_snapshot rather than
> screenshots.

## Build and test

`sim_build_run` and `sim_test` run commands, so they are off by default in a standalone install:

```sh
sim-mirror config set build.tools true
```

They build the project in the agent's root folder with xcodebuild for its simulator, install and launch it, and answer
with only what failed and where.

## Tips

- Ask for snapshots, not screenshots; see [Screen understanding](../screen-understanding.md) for why.
- A tool that says its connector cannot do something means the device is shown view-only -- neither the native
  helper nor idb_companion could be used: run `sim-mirror doctor`.
- `sim-mirror tools` lists what this install offers.
