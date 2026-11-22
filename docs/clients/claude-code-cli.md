# Claude Code in a terminal

Claude Code drives the simulator through SimMirror's MCP tools; you watch, and step in, in a browser tab.

## Install with the plugin

```
/plugin marketplace add AndrewKochulab/sim-mirror
/plugin install sim-mirror@sim-mirror
```

The plugin brings:

- the tools, from `sim-mirror mcp` run with uvx at the release's tag;
- four [workflow skills](skills.md): interaction, setup/recovery, builds/tests and evidence capture;
- **`/sim-mirror:open`**, which opens this project's viewer.

Its server is `device`, which Claude Code shows as `plugin:sim-mirror:device`, and its tools are named
`mcp__plugin_sim-mirror_device__sim_snapshot` and so on. To try a local copy of the plugin
instead, start Claude Code with `claude --plugin-dir plugins/sim-mirror`.

## Install without the plugin

```sh
claude mcp add sim-mirror -- uvx --from sim-mirror==3.9.0 sim-mirror mcp
```

`--scope project` (before the name) writes it to the project's `.mcp.json`, for everyone working on it. The tools are
named `mcp__sim-mirror__sim_snapshot` and so on.

## Which project

`sim-mirror mcp` gives the agent **the scope of the folder Claude Code runs in** -- its own simulator -- and that
folder as the only place `sim_app install` and the build tools may reach. To choose otherwise:

```sh
claude mcp add sim-mirror -- uvx --from sim-mirror==3.9.0 \
  sim-mirror mcp --scope my-app --root /Users/me/Projects/my-app
```

### Git worktrees

An agent often works in a git worktree of the project -- a second checkout, on another branch -- after the session
started in the main checkout. `sim_build_run` and `sim_test` build the folder the agent works in, not the one the
server started in:

1. the folder a call names in `folder`;
2. else the folder the agent named in an earlier call: once named, a folder is kept for the agent's later builds, test
   runs, `sim_app info` and the relative `save_to` paths of `sim_screenshot`, until it names another or its session
   ends;
3. else the folder the client says it works in (its MCP roots), when the client offers them;
4. else the folder `sim-mirror mcp` started in, or its first `--root`.

Claude Code's `EnterWorktree` moves its MCP roots into the worktree, but sends no `notifications/roots/list_changed`
when it does (measured with Claude Code 2.1.301), so the relay asks the client for its roots again before every tool
call: one local round trip, which Claude Code answers at once. A client that does not answer within two seconds has
the call go with the roots it gave last, and is not asked before a call again. A `cd` in the shell moves nothing: the
roots stay where the session is, so an agent that changed folders that way names the worktree in `folder` -- once.

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

The device and its scope stay the session's, and so do its build logs and, by default, its DerivedData: every checkout
the session builds shares one DerivedData folder, the scope's (`build.derived_data = "scope"`). Switching between
worktrees costs a rebuild of what differs, but a folder per worktree costs more where one session works in one
worktree at a time. An iOS app's DerivedData is gigabytes -- its products, index, module cache and Swift packages --
and agents make worktrees freely, one per task; each new one resolves and builds every package from nothing. One
folder per scope keeps the packages and module cache warm, and since it runs one build at a time, sharing it is safe.
When a stale product is suspected, delete the folder (`~/Library/Developer/Xcode/DerivedData/SimMirror-<scope>`); the
next build makes it again.

Where several sessions build different worktrees at once, `build.derived_data = "checkout"` gives each checkout a
DerivedData folder of its own, beside the scope's and named after it -- for the worktree
`/work/demo-worktrees/feature-x`, `SimMirror-<scope>-feature-x-<hash of its path>` -- so their builds run side by side
instead of one waiting for the other, and none undoes what another compiled. Each folder holds a
`sim-mirror-checkout.txt` naming its checkout, and a build deletes the scope's folders whose checkout is gone, as
removing a worktree leaves it:

```sh
sim-mirror config set build.derived_data checkout --scope project-demo-1a2b3c4d
```

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

### Several sessions at once

Two Claude Code sessions in one project -- one per worktree, say, both started in the main checkout -- are one scope,
but each `sim-mirror mcp` is its own agent connection, and each works on **a device of its own**
(`device.selection = "agent"`, the default):

- An agent works on the device it chose with `sim_device choose`, else on the one its first call that needed a device
  was given: the project's own device, the one the viewer shows. Every later call -- `sim_snapshot`, `sim_act`,
  `sim_app`, `sim_record`, the build tools -- acts on that one, whatever another session chooses meanwhile.
- `sim_device choose` moves only the agent that calls it. The project's own device moves with it, and the viewer
  follows, only while it is the only live agent of the project, or when it passes `default: true`. The device it
  leaves is let go when nobody else works on it, nothing builds on it and it is not the project's own. A person
  picking a device in the viewer moves the project's own device, and the agents working on the old one with it.
- With `device.agent_default = "free"`, an agent that has chosen nothing is given a simulator nobody uses while another
  agent works on the project's own, rather than sharing it. Only a simulator or an emulator already on this Mac is
  given -- none is created -- and each one booted takes 2-3 GB of memory (`device.max_booted`, 2, bounds how many run).
- Before an agent acts on a device another live agent works on, it is told: a call that would change it -- a tap, a
  launch, a build, a recording, a choose -- is refused up front (`device.exclusive = "all"`, the default) unless it
  says `steal: true`, and any other call starts its answer with a note such as
  `note: Claude Code #3fa2 works on this device (last call 40 s ago); sim_device list shows free ones`. `sim_device
  list` marks the agent's own device `in use here`, the project's own, and each one another agent works on.
- `sim_record stop` stops only a recording the agent started itself, unless it says `steal: true`.
- Each session builds its own worktree onto its own device. It names the worktree once, with `folder` on a build
  call, and its later builds, test runs, `sim_app info` and relative `save_to` paths go there too. With the scope's
  one DerivedData folder, a build of another worktree waits for the one running and is told so; with
  `build.derived_data = "checkout"` they run side by side. Two builds for one device never run at once, and from the
  build through the install and launch of its app the device is busy: no gesture, `sim_device choose`, restart or
  stop lands on it.
- `sim_test` may send its tests to another of the project's devices with `destination` once the agent working on it
  has gone quiet; while that agent is live, the answer names it.

A live agent is one with a call under way, or one made within `device.agent_idle_minutes` (30); its connection ending
forgets it at once. Each agent is named by its client and a label: `--label` or `SIM_MIRROR_AGENT_LABEL` when its
session was started with one, else four hex digits of its connection's token -- `Claude Code · ticket-123`,
`Claude Code #3fa2`. While other agents work in the project too, `sim_device info` says what the asking agent is called
and who else works on its device. A session started with `--root` builds in its own first `--root`, not in another
session's of the same project. The label travels in a header, from the relay's environment, never on a command line:

```sh
SIM_MIRROR_AGENT_LABEL=ticket-123 claude
```

#### Watching two devices side by side

A viewer tab shows the project's own device and follows it. To watch each session's device, open one tab per device:
`sim-mirror devices` lists the UDIDs, `sim_device list` says which device each agent works on, and

```sh
sim-mirror open --device 00000000-0000-0000-0000-000000000001
sim-mirror open --device 00000000-0000-0000-0000-000000000002
```

opens `/viewer/<scope>?device=<udid>` for each. A tab of its own device stays on it whatever an agent or a person
picks, comes back to it after a restart, and has the device's name in its title. Its picker says which agent works on
each device; picking a row shows that device in this tab only (Show in this tab, which moves no agent), **Use for the
project** makes it the project's own -- the tabs following the project and the agents on the old one move to it -- and
**Follow the project** turns the tab back into one that follows the project's own device.

`sim-mirror config set device.selection scope` gives every agent of a scope its one shared device again, as before
3.9: a choose moves them all. `device.exclusive = "real_devices"` refuses another agent only on a real device, and
`off` never; both still say who else works on the device.

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
`mcp__sim-mirror` (or `mcp__plugin_sim-mirror_device` for the plugin). Up to 2.2 the plugin's server was named
`sim-mirror`, shown as `plugin:sim-mirror:sim-mirror`: a rule or a hook that names `mcp__plugin_sim-mirror_sim-mirror`
names `mcp__plugin_sim-mirror_device` now.

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
