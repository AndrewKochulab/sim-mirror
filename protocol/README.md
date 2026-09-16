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
| `v1/client-input.schema.json` | `TouchInput`, `ScrollInput`, `ButtonInput`, `KeyInput`, `TextInput`, `AppearanceInput` |
| `v1/settings.schema.json` | The settings panel's HTTP shapes: `SettingsView`, `SettingEntry`, `RuleSpec`, `SettingsChange`, `SettingsRefusal`, and the `SettingEffect`, `SettingReach`, `SettingLayer`, `SettingsAccess` and `SettingsTarget` enums |
| `v1/constants.json` | `PROTOCOL_VERSION`, binary frame tags, message and text limits, ticket and hello timeouts |
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
   agent gesture, and **binary frames**: the first byte is `TAG_JPEG` (a whole JPEG image) or `TAG_H264` (H.264
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

When these rules start binding — they do not yet, while SimMirror is `0.x` — and what else is promised alongside
them, is [what SimMirror promises not to break](../docs/stability.md).
