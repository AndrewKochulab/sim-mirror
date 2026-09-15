# Writing a connector

A connector reaches a device, says what it can do there, and hands over the parts SimMirror drives. Read
[Connectors](../connectors.md) first, and start from the
[example connector](../../examples/custom-connector/), which is complete and tested.

## The protocol

```python
class Connector(Protocol):
    @property
    def name(self) -> str: ...
    async def probe(self, config: SimConfig) -> ConnectorReport: ...
    async def attach(self, udid: str, config: SimConfig) -> DeviceSession: ...
    async def reap_orphans(self) -> int: ...
```

### `name`

Lower-case letters, digits, `_` and `-`, starting with a letter: how `connectors.preferred`, the doctor and the viewer
name it.

### `probe(config)`

Whether it can be used on this Mac with these settings. Called often -- by the doctor, by `auto` selection -- so keep it
cheap and side-effect free: look for binaries and versions, never start anything.

- Available: `ConnectorReport(name, True, capabilities, versions)`, with no reasons.
- Not available: `ConnectorReport(name, False, reasons=(...))`, each reason something a person can act on ("idb_companion
  is not installed: brew install facebook/fb/idb-companion").

### `attach(udid, config)`

Reach a **booted** device and answer a `DeviceSession`:

| Field | Give |
|---|---|
| `connector` | Your `name` |
| `capabilities` | Exactly what `probe` promised |
| `screen` | A `ScreenSource`: `describe()` (size in pixels and points, scale), `screenshot(max_width, quality, crop)`, and `h264(...)` if you promise `stream_h264` |
| `input` | An `InputSink` (`hid(events)`) if you promise any `input_*` capability |
| `reader` | A `ScreenReader` (`accessibility()`) if you promise `element_tree` |
| `fps_limit` | The most frames a second you can stream, or `None` |
| `is_alive` | Whether what the session relies on -- a helper process -- still runs |
| `on_close` | What to end when the session is let go; closing twice must be closing once |

Coordinates are the device's **points**, in portrait. Raise `ConnectorUnavailable(message, status)` when the device
cannot be reached.

### `reap_orphans()`

End what an earlier run of *this host* left running for your connector, and answer how many. Use the host's owner tag
(`ConnectorContext.state.owner_tag`) in whatever you leave on disk, so you never end another host's processes.

## The factory and the entry point

```python
def create(context: ConnectorContext) -> MyConnector:
    return MyConnector(state=context.state, copy=context.copy, simctl_for=context.simctl_for)
```

```toml
[project.entry-points."sim_mirror.connectors"]
mine = "my_package.connector:create"
```

`ConnectorContext` gives you where the host keeps state and runs sockets (`state`), its wording (`copy`), and simctl for
a developer directory (`simctl_for`). A connector named like a built-in one is ignored, and one that fails to load is
logged and left out.

## Rules

- **Start processes in a group of your own** and end the group; keep sockets in `state.run_dir()` (short paths: 104
  bytes at most) and logs in `state.log_dir()`.
- **Never listen on TCP** where a unix socket will do.
- **Secrets never go in argv.**
- **Say why**: every refusal names what to do.

## Testing

`sim_mirror.testing.contract.check_connector(connector, config, udid)` checks every promise at once -- the report, each
role, closing twice, reaping -- against your own fakes, and lists everything broken:

```python
async def test_my_connector_keeps_the_contract() -> None:
    assert await check_connector(MyConnector(fake_process_layer()), SimConfig.defaults(), "some-udid") == []
```

Never start a real simulator or helper from a test: fake the layer that starts processes.

## Contributing a connector here

Open an issue first. A built-in connector lives in `src/sim_mirror/connectors/<name>/`, is added to
`scripts/check_containment.py`'s allowed modules if it runs a program, passes `tests/contract/`, and gets rows in
`compat/matrix.toml`.
