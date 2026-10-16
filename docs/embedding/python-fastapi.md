# A Python host application

An application with its own server can run SimMirror **inside** its process instead of beside it: its own paths, its
own sign-in, its own idea of what a scope is. No daemon runs.

When several applications on one Mac show simulators, they can instead [share the daemon](shared-daemon.md), each with
a host token for its own scopes.

[examples/fastapi-embed](../../examples/fastapi-embed/) is a complete, tested example.

## The surface

A host imports **only `sim_mirror.api`**. Everything named there is kept stable across minor versions; the rest of the
package may change.

| Name | What it is |
|---|---|
| `Runtime` | One SimMirror for the process: `Runtime.build(...)`, `start()`, `reconcile(group)`, `close()` |
| `Scope` | What devices, settings and agents are grouped by: `Scope(id, group, label)` |
| `ConfigSource`, `StateStore`, `Policy`, `Authenticator`, `UsageProbe`, `DeviceMemory` | The seams a host implements |
| `InputPause` | An optional seam: a person's "hands off" for agents |
| `SimConfig`, `HostCopy` | Settings, and the words messages use |
| `Person`, `Caller`, `Admission`, `Refused`, `HeldDevice` | What an authenticator answers, and how it refuses |
| `create_http_router`, `create_socket_router`, `create_agent_router` | The routes to mount |
| `relay_command` | The command line an MCP client runs to reach the agent routes |
| `SimulatorUnavailable` | Why a scope cannot have a simulator now |

## Scopes

A scope's `id` is 1 to 128 letters, digits and `_ . : -`, starting with a letter or digit, so it is safe in a URL path
and a file name. In `device.mode = "per_scope"` every scope gets a device of its own; in `"shared"` a whole `group`
shares one. The `label` names the device: `<device.name_prefix> · <label>`.

## The seams

| Seam | Answers | Called |
|---|---|---|
| `ConfigSource.get(scope)` | The scope's `SimConfig` now | On every operation: keep it cheap |
| `StateStore` | Where builds are kept, where sockets, logs and claims live, and the host's owner tag | When a device is brought up or a build runs |
| `DeviceMemory` | Which simulator a scope uses, remembered between runs | When a device is resolved or picked |
| `Policy` | Whether a scope may have a simulator at all, run commands, install from which folders, build in which | Before each of those |
| `Authenticator` | Who a person's request, an agent's call and a screen socket are, or `Refused(status, message)` | Before anything else on every route |
| `UsageProbe` | Whether an agent holds a device, so it is not stopped as idle | By the reaper |
| `InputPause` (optional) | Whether a person has paused agents' gestures on a device -- a real phone in their hand | Before each batch of agent steps, and each gesture in it |
| `HostCopy` | The words for where settings are, what a scope is called, and the doctor hint | In every message |

Share the **claims** folder with every other SimMirror on the Mac: answer `StateStore.claims_dir()` with
`claims_dir(os.environ)` rather than writing the path out, and two hosts will refuse to drive one device instead of
fighting over it.

`DeviceMemory` is the only seam with a ready-made implementation: `JsonDeviceMemory(path)` keeps every scope's device
in one private JSON file. Pass your own when you have somewhere better — a row per project in your database — and
then nothing about files reaches your `StateStore` at all.

## Real devices

Real devices, agents choosing one and WebDriverAgent are off for an embedding host until it turns them on
(`SimConfig.defaults("embedded")`); a person turns them on and picks the device. What the host can then rely on is
checked by `tests/contract/test_physical_embedding.py`, through this surface alone:

- a scope keeps the phone it picked, and while the phone is not there it is refused with why -- never given a
  simulator in its place; two scopes `may_share` keeps apart, or another host on the Mac sharing the claims folder,
  never drive one phone at once;
- what an agent is offered follows what the phone can do now: view only until WebDriverAgent is set up, touchable
  once its session is started again (`sim_device restart` on a real device does that, and nothing else);
- `real_devices.enabled` turned off ends a phone's session at once, even during a call, its screens closed with why;
- a viewer built before real devices still watches and touches a phone, its input comes before an agent's, and every
  agent event names the agent as the `Authenticator` said, never as the agent says;
- what was changed on a phone is put back when it is let go -- after it was unplugged, or after a crash, once it is
  back -- and nothing ever asks a phone to shut down, restart or be erased.

SimMirror sees a person's input only through a viewer: a finger on the phone's own screen is not seen, so an agent is
not held back by it. A host that can tell -- its own button, its own sense of who has the phone -- passes an
`InputPause`: while its `paused(device)` answers true, agents' gestures stay off that device
([Real devices](../real-devices.md#a-person-comes-first)). A host that shares the daemon has no pause yet.

## Putting it together

```python
from sim_mirror.api import (
    JsonDeviceMemory, Runtime,
    create_agent_router, create_http_router, create_socket_router,
)

runtime = Runtime.build(
    config=MyConfig(),
    state=MyState(),
    policy=MyPolicy(),
    memory=JsonDeviceMemory(MY_STATE_ROOT / "devices.json"),
    copy=MyCopy(),
)
auth = MyAuthenticator()
source = lambda: runtime

app.include_router(create_http_router(source, auth), prefix="/api/projects/{scope_id}/simulator")
app.include_router(create_socket_router(source, auth), prefix="/api/projects/{scope_id}/simulator")
app.include_router(create_agent_router(source, auth), prefix="/api/agent/simulator")
```

Start the runtime once the event loop runs (`await runtime.start()`, which also ends what a crashed earlier run left
behind), call `await runtime.reconcile(group)` **before** answering a settings change -- so a switch that is off is off
by then -- and `await runtime.close()` on the way out.

## The routes

Under the prefix you give each router:

| Route | Does |
|---|---|
| `GET ""` | The scope's status: whether it can have a simulator, why not, and its device |
| `POST ""` | Bring the device up and mint a one-shot ticket for its screen socket |
| `DELETE ""` | Let the device go; `?shutdown=true` shuts it down too |
| `GET /devices`, `PUT /device` | This Mac's simulators, and choosing one |
| `WS /screen?ticket=…` | The [screen protocol](../reference/protocol.md) |
| `GET /manifest`, `POST /call` | The agent routes the relay calls |

Person routes answer `{"ok": true, "data": …}`; refusals are HTTP errors with a `detail`.

## Settings

The viewer's [settings panel](../settings.md) appears wherever its transport can read settings. A host that already
has settings screens mounts nothing here, and the panel never shows. A host that wants it mounts one more router:

```python
app.include_router(
    api.create_settings_router(source, store, auth, confirmations=confirmations, daemon_scope=daemon_scope),
    prefix="/api/projects/{scope_id}/simulator/settings",
)
```

- `store` is a `SettingsStore`: where each value a scope sees comes from (`SettingOrigin`), and writing a change for
  one scope or every scope, raising `SettingsRefused` with each setting's problem.
- `auth` is a `SettingsAuthenticator`: a `SettingsEditor` saying whether the asker may read, change, or change the
  sensitive settings without confirming. Keep settings to your own pages: the router trusts what it is told.
- `confirmations`, a `Confirmations`, holds a sensitive change until someone confirms it somewhere a page cannot
  reach. Without one, a page is simply refused those settings.
- `daemon_scope` is the scope you read your own server-wide settings as.

`sim_mirror.testing` has `MemorySettingsStore` and `FakeConfirmations` for a host's tests. On the viewer side, a
transport offers the panel by having `settings()` and `changeSettings()`; `createHttpTransport` has them with
`settings: true`.

## Giving agents the tools

```python
from sim_mirror.api import relay_command

argv = relay_command(
    url_env="MY_TOOLS_URL",                                        # where the agent routes are
    headers={"X-My-Session": "MY_SESSION", "X-My-Scope": "MY_SCOPE"},  # header name -> environment variable
    client_header="X-My-Client",
    server_name="simulator",
)
```

The relay is one standard-library file run isolated (`python -I`); it reads the URL and every credential from the
environment, never argv, talks only to loopback addresses and ignores proxy settings.

With `roots_header="X-My-Roots"`, the relay asks a client that offers MCP roots for the folders it works in -- once it
is initialized, when it says they changed, and before each tool call -- and sends them with each request: absolute paths, each percent-encoded, separated by commas. An authenticator that reads them
puts them in `Caller.roots`, and `sim_build_run` and `sim_test` then build the one of them that is the build folder's
or another checkout of its repository, such as a git worktree (`Policy.build_folder` stays the boundary).

## Testing a host

`sim_mirror.testing` ships the fakes SimMirror's own suite uses: `DeviceRig` (a real `DeviceManager` over a fake Mac),
`FakeConnector`, `FakeXcrun`, `StaticConfig`, `MemoryStateStore`, `FakePolicy`, and `AsgiSocket` for screen sockets.
`sim_mirror.testing.guards` refuses to start Xcode's tools, idb_companion or an agent from a test.

## Before you ship

The routers do not check `Host` and `Origin`, CORS or framing: that is the host's middleware. The standalone daemon's
rules are a good start; see [Security](../security.md).
