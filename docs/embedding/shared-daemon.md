# Sharing the daemon between hosts

An application can use SimMirror without running one of its own: it shares the local daemon (`sim-mirror serve`) with
the command line and with other applications. Each gets a **host token** for its **namespaces**, and reaches only the
scopes in them, the devices those scopes run, and the tokens it made.

Choose this over [running SimMirror inside your process](python-fastapi.md) when several applications on one Mac show
simulators, and one daemon should decide how many are booted and who has which.

## Making a host

The person who installed SimMirror makes the token once, and hands it to the application's backend:

```sh
sim-mirror token create --kind host --scope 'notes:*' --root ~/Projects --label "Notes app"
```

It prints the token once, on stdout, and its id on stderr. The host now has every scope whose id starts `notes:` --
`notes:42`, `notes:checkout` -- and its agents may be given folders inside `~/Projects`. No two hosts share a
namespace. `sim-mirror token revoke <id>` revokes the host and every token it made.

## What a host may do

| In its namespaces | Not at all |
|---|---|
| A scope's status; bring its device up and let it go; its devices, and choosing one | Another host's scopes, or the Mac's own |
| Embed tickets, for a page to frame a scope's screen | The admin routes: login codes, reload, anyone's tokens |
| Its scopes' settings, one scope at a time; a sensitive one waits for `sim-mirror settings confirm` | Settings for every scope, or the daemon's own |
| Make `agent` and `viewer` tokens for its scopes, with folders inside its own; list and revoke those | Make `admin` or `host` tokens |

**Devices stay apart.** A scope never joins or picks a simulator a scope of another host -- or of the Mac itself -- is
running, and another host's running simulators are left out of its picker. Once one is let go, anyone may use it.

**A host's scopes are its own group**, named for the namespace, so `device.mode = "shared"` gives each host one device,
apart from the Mac's.

**What stays shared** is the daemon itself: `device.max_booted` counts every host's devices, `server.*` and
`security.*` are the daemon's, and settings a scope has not set come from config.toml, as for any scope.

## From Python

`sim_mirror.api.DaemonHost` is the whole of it, over the standard library's HTTP:

```python
from sim_mirror.api import DaemonHost

host = DaemonHost("http://127.0.0.1:7466", token=NOTES_TOKEN, token_id=NOTES_TOKEN_ID)

host.start("notes:42")                       # bring the device up
page_url = host.embed_url("notes:42")        # frame this; its ticket works once, within a minute
host.change_settings("notes:42", {"stream.fps": 20})

access = host.agent("notes:42", label="Notes agent", roots=["/Users/me/Projects/Notes"])
# Give an MCP client access.argv as the server's command and access.env as its environment:
# the token is in the environment only. Renew its lease while it runs, and revoke it after.
host.keep(access)
host.revoke(access.token_id)
```

Before its first request, `DaemonHost` sends `/healthz` a fresh nonce and its token's id, and sends its token only when
the answer proves the listener knows that token -- the daemon keeps its digest; nothing else on the port has it. A
refusal raises `DaemonRefused` with the status and what the daemon said; a daemon that is not there, or that did not
prove itself, raises `DaemonUnavailable`.

## The routes

For a backend in another language, the same, with `Authorization: Bearer <host token>`:

| Route | Does |
|---|---|
| `GET /healthz?nonce=N&token_id=ID` | `token_proof`: HMAC-SHA256 of `sim-mirror/healthz:` + N, keyed by the hex SHA-256 of the token |
| `GET, POST, DELETE /api/v1/scopes/{scope}` | Status, start (with a screen ticket), stop (`?shutdown=true`) |
| `GET /api/v1/scopes/{scope}/devices`, `PUT …/device` | The picker's devices, and choosing one |
| `POST /api/v1/scopes/{scope}/embed-tickets` | A frame's URL, with a one-shot ticket in its fragment |
| `GET, PATCH /api/v1/scopes/{scope}/settings` | Settings, with `"target": "scope"` |
| `GET /api/v1/host` | This host's record: namespaces, folders, label |
| `POST, GET /api/v1/host/tokens`, `DELETE …/tokens/{id}` | Make, list and revoke its `agent` and `viewer` tokens |

An agent token a host made works like any other: `/api/v1/agent/manifest`, `call` and `lease`, with the scope in
`X-SimMirror-Scope`.

The host routes, `DaemonHost` and the names beside it are **preview** until 1.0: see [Stability](../stability.md).
