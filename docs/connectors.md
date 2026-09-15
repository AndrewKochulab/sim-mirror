# Connectors

A connector is how SimMirror reaches a device: it says what it can do there, and hands over the parts to drive it.
Booting, installing and launching are simctl's whichever connector is in use; a connector owns the screen, input and
the element tree.

## Capabilities

| Capability | Means |
|---|---|
| `lifecycle` | Boot, shut down and restart devices |
| `device_list` | List this Mac's simulators |
| `appearance` | Switch light and dark |
| `open_url` | Open a URL on the device |
| `app_install`, `app_launch` | Install a built app; launch and terminate apps |
| `logs` | Read the device's log |
| `screenshot` | A JPEG of the screen |
| `stream_jpeg`, `stream_h264` | The live screen as JPEG frames or as H.264 |
| `input_touch`, `input_button`, `input_key`, `input_text` | Touches and gestures, hardware buttons, keys, text |
| `element_tree` | What is on screen, as an accessibility tree |
| `build_preview` | Reserved for build integrations |

The viewer offers only the controls the connector can serve, and an agent is offered only the tools it can: without
`input_touch` there is no `sim_act`, without `element_tree` no `sim_snapshot`. See the
[tool reference](reference/tools.md) for what each tool needs.

## Built in

| Connector | Needs | Can do |
|---|---|---|
| `idb` | idb_companion 1.5, from Homebrew or `connectors.idb.companion_path` | Everything above except `build_preview`: JPEG and H.264, all input, the element tree |
| `simctl` | Only Xcode | View-only: JPEG at up to 4 frames a second, lifecycle, device list, appearance, open URL, install, launch, logs, screenshots |

The idb connector starts one idb_companion per booted device, in a process group of its own, serving on a unix socket
in the run folder -- no TCP port -- and ends it when the device is let go. A companion a crashed SimMirror left behind is
ended the next time it starts, and never one another host started.

## Choosing one

`connectors.preferred`:

- **`auto`** (the default) tries `idb`, then `simctl`, and uses the first that can be used here. When it falls back it
  says why -- in the viewer, in `sim-mirror doctor`, and in the refusal of a tool that needs more -- so a Mac without
  idb_companion still shows the screen and says what to install to touch it.
- **A connector's name** uses that one, or refuses with its reasons. It never quietly uses another.

Changing it takes effect at once: a running device is brought back on the new connector.

## More connectors

A package registers a connector under the `sim_mirror.connectors` entry point, and SimMirror finds it when it starts;
`sim-mirror version` and `sim-mirror doctor` list it. Choose it by name in `connectors.preferred`.

Planned: a native Swift helper (faster frames and input without idb_companion), Xcode 27's `mcpbridge`, real iPhones
(first without signing, then WebDriverAgent), and Android emulators. Write your own with the
[connector guide](contributing/connector-guide.md).
