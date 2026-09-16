# A Python host application

An application with its own server can run SimMirror **inside** its process instead of beside it: its own paths, its
own sign-in, its own idea of what a scope is. No daemon runs.

[examples/fastapi-embed](../../examples/fastapi-embed/) is a complete, tested example.

## The surface

A host imports **only `sim_mirror.api`**. Everything named there is kept stable across minor versions; the rest of the
package may change.

| Name | What it is |
|---|---|
| `Runtime` | One SimMirror for the process: `Runtime.build(...)`, `start()`, `reconcile(group)`, `close()` |
| `Scope` | What devices, settings and agents are grouped by: `Scope(id, group, label)` |
| `ConfigSource`, `StateStore`, `Policy`, `Authenticator`, `UsageProbe`, `DeviceMemory` | The seams a host implements |
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
| `HostCopy` | The words for where settings are, what a scope is called, and the doctor hint | In every message |

Share the **claims** folder with every other SimMirror on the Mac: answer `StateStore.claims_dir()` with
`claims_dir(os.environ)` rather than writing the path out, and two hosts will refuse to drive one device instead of
fighting over it.

`DeviceMemory` is the only seam with a ready-made implementation: `JsonDeviceMemory(path)` keeps every scope's device
in one private JSON file. Pass your own when you have somewhere better — a row per project in your database — and
then nothing about files reaches your `StateStore` at all.

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

## Testing a host

`sim_mirror.testing` ships the fakes SimMirror's own suite uses: `DeviceRig` (a real `DeviceManager` over a fake Mac),
`FakeConnector`, `FakeXcrun`, `StaticConfig`, `MemoryStateStore`, `FakePolicy`, and `AsgiSocket` for screen sockets.
`sim_mirror.testing.guards` refuses to start Xcode's tools, idb_companion or an agent from a test.

## Before you ship

The routers do not check `Host` and `Origin`, CORS or framing: that is the host's middleware. The standalone daemon's
rules are a good start; see [Security](../security.md).
