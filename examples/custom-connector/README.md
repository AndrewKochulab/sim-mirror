# Custom connector

A connector is how SimMirror reaches a device. SimMirror ships two -- `idb` (full control, through idb_companion) and
`simctl` (view-only) -- and finds more through the `sim_mirror.connectors` entry point.

[recorded_connector.py](recorded_connector.py) is a complete one that needs nothing installed: its "device" is a folder
of JPEG screenshots, shown one after another. Use it as the skeleton for a real one.

## The shape

| Part | Does |
|---|---|
| `name` | How settings, reports and the viewer name it |
| `probe(config)` | Whether it can be used here, its capabilities and versions, and **why not** when it cannot |
| `attach(udid, config)` | A `DeviceSession`: the capabilities, and a role object for each group of them -- `screen` always, `input` for `input_*`, `reader` for `element_tree` |
| `reap_orphans()` | Ends processes an earlier run left behind, and answers how many |
| `create(context)` | The entry point: gets the host's state folders, wording and simctl |

SimMirror offers exactly what the capabilities promise: no touch tools and no input from the viewer without
`input_touch`, no snapshots without `element_tree`. A connector that cannot be used says why, and `sim-mirror doctor`
shows it.

## Test it

`sim_mirror.testing.contract.check_connector` checks every promise at once -- the report, each role, closing twice --
against your connector's own fakes. [test_recorded_connector.py](test_recorded_connector.py) uses it:

```sh
cd examples/custom-connector
uv run --group dev pytest
```

## Use it

```sh
uv pip install ./examples/custom-connector     # into the environment SimMirror runs in
```

```toml
# SimMirror's config (`sim-mirror config path`)
[connectors]
preferred = "recorded"
```

and start the daemon with `RECORDED_FRAMES_DIR=/path/to/frames` in its environment. The viewer shows the frames,
view-only, and says which connector it is using.
