# SimMirror protocol

This folder is the single definition of how a SimMirror viewer talks to a SimMirror server: the screen socket's
messages, the shapes the HTTP routes answer with, the close codes and the constants. Everything else is generated from
it or tested against it:

- `src/sim_mirror/protocol/_generated.py` and `viewer/src/protocol.generated.ts` are written by
  `scripts/gen_protocol.py` (`make generate`), and CI fails when either is stale.
- The contract tests in `tests/contract/` validate every message the server builds against these schemas.

Human-readable reference: [`docs/reference/protocol.md`](../docs/reference/protocol.md).

## Version 1

| File | Defines |
|---|---|
| `v1/common.schema.json` | `Device`, `Screen`, `Size`, `Agent`, `Share`, and the `Capability`, `Encoding`, `EncodingSetting` and `DeviceState` enums |
| `v1/hello.schema.json` | `ServerHello`, `ClientHello` |
| `v1/stream.schema.json` | `StreamStart` |
| `v1/status.schema.json` | `StatusEvent`, and the HTTP shapes `ScopeStatus`, `Started`, `DeviceChoice` |
| `v1/agent-event.schema.json` | `AgentIntent`, `AgentDone` |
| `v1/screen-text.schema.json` | `ScreenText`, `TextBox`: the text a reading of the screen's pixels found, for a viewer to outline |
| `v1/client-input.schema.json` | `TouchInput`, `ScrollInput`, `ButtonInput`, `KeyInput`, `TextInput`, `AppearanceInput` |
| `v1/http.schema.json` | What the daemon's routes answer inside `{"ok": true, "data": …}` -- `Health`, `Stopped`, `DeviceList`, `Chosen`, `EmbedTicket`, `Exchanged`, `Lease`, `TokenRecord`, `MadeToken`, `TokenList`, `Revoked` -- the agent routes' `AgentManifest` and `ToolResult`, a `Refusal`, and the `TokenKind` and `SessionKind` enums |
| `v1/settings.schema.json` | The settings panel's HTTP shapes: `SettingsView`, `SettingEntry`, `RuleSpec`, `SettingsChange`, `SettingsRefusal`, and the `SettingEffect`, `SettingReach`, `SettingLayer`, `SettingsAccess` and `SettingsTarget` enums |
| `v1/constants.json` | `PROTOCOL_VERSION`, binary frame tags, message, text and text-box limits, ticket and hello timeouts |
| `v1/close-codes.json` | WebSocket close codes and what each means |

### A screen socket's life

1. The client asks the HTTP API to start a scope's device and receives a one-shot **ticket** (valid for
   `TICKET_TTL_S` seconds).
2. It opens the screen socket with that ticket. The server admits it or closes it with `CLOSE_UNAUTHORIZED` or
   `CLOSE_FORBIDDEN`.
3. The server sends a `ServerHello`. The client answers with a `ClientHello` within `HELLO_TIMEOUT_S` seconds.
4. The server sends `StreamStart` with the first encoding in the client's list that it offers, or closes with
   `CLOSE_UNSUPPORTED` when there is none, and with `CLOSE_BAD_MESSAGE` when the answer is not a hello.
5. From then on the server sends a `StatusEvent` first and whenever the device changes, an `AgentEvent` around every
   agent gesture, a `ScreenText` after each reading of the screen's pixels while the overlay is on -- and one with no
   boxes when what it said no longer holds -- and **binary frames**: the first byte is `TAG_JPEG` (a whole JPEG image) or `TAG_H264` (H.264
   Annex-B bytes; a client's first H.264 frame is always a sync point with parameter sets).
6. The client sends `ClientInput` messages. Anything else, any message over `MESSAGE_MAX_BYTES`, and any input the
   device cannot take is ignored.
7. The server closes with `CLOSE_STOPPED` when the device is stopped and `CLOSE_RESTARTING` when it is restarting (a
   client reconnects after the latter).

### Rules

- **Every property is always sent.** A value that may be missing is `null`, never absent.
- **Receivers ignore properties they do not know.** Within version 1, messages only gain properties.
- **A change that would break a version 1 receiver is version 2**, served beside version 1 for at least one minor
  release.
- Enum definitions carry an `x-constant` naming the tuple (Python) or `as const` array (TypeScript) of their values.
- Read enums as **open**: a new capability or encoding may appear within version 1, and a client is only ever sent an
  encoding it asked for in its hello.

These rules bind from SimMirror 1.0, and `compat/surface-v1.json` holds every schema, constant and close code here to
them: `scripts/surface.py` fails when one is broken. What else is promised alongside them is
[what SimMirror promises not to break](../docs/stability.md).

## App SDK protocol, version 1

`app-sdk/v1/` is how an app built with SimMirror's debug SDK hands SimMirror its view hierarchy, so a screen whose
controls have no accessibility labels still reads well. It is a protocol of its own, with its own version: an app
speaks it, not a viewer. The Swift SDK and the daemon both test against these schemas and the examples beside them.

| File | Defines |
|---|---|
| `app-sdk/v1/listing.schema.json` | `Listing`: the file a running app writes to say where it listens |
| `app-sdk/v1/hierarchy.schema.json` | `Hierarchy`, what the app answers, with `App`, `Screen`, `Frame`, `Modal`, `Keyboard`, `Window`, `Node`, the `Kind`, `LabelSource`, `Trait`, `NodeSource`, `ModalKind` and `Orientation` enums, and `ErrorBody` with its `ErrorCode` |
| `app-sdk/v1/examples/` | A listing and two refusals, and hierarchies recorded from `examples/app-sdk` on iOS 26.5 -- its UIKit form, SwiftUI and tagged screens, the keyboard and an alert -- which both sides check themselves against |

### An exchange

1. A debug build of the app on a simulator starts listening on `127.0.0.1` on a port of its own, and writes a
   `Listing` -- its port, its process and a secret new each launch -- to
   `<simulator data>/Library/Caches/SimMirror/apps/<bundle_id>.json`, readable by its owner only. It writes it again
   whenever it comes to the front or leaves it.
2. To read the screen, SimMirror reads the listings in the simulator's data folder, and asks the apps in front
   `GET /v1/hierarchy?max_nodes=<n>` with `Authorization: Bearer <secret>`, one request per connection.
3. The app in front answers a `Hierarchy`: its windows' views as nodes, each with a kind, a label it worked out, and
   a frame in points on the screen held upright. An app that is not in front answers `409` at once. A request without
   the secret is answered `401` before anything else, and one with an `Origin` header, or a `Host` other than
   `127.0.0.1:<port>`, is refused.

### Rules

The same as version 1's: every property is always sent, receivers ignore properties they do not know, enums are read
as open, and what version 1 requires stays required (`tests/contract/test_app_sdk_schemas.py` holds it). The app SDK
protocol is a preview in SimMirror 1.x and is not part of `compat/surface-v1.json`; a change that would break an app
built with an earlier SDK is its version 2.
