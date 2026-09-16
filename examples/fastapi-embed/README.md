# FastAPI embed

A Python application with SimMirror inside it: [app.py](app.py). No daemon runs; the application builds SimMirror's
`Runtime` from its own seams and mounts the three routers under its own paths.

```sh
cd examples/fastapi-embed
EXAMPLE_KEY=change-me uv run --with "sim-mirror==0.2.0" \
  uvicorn --factory app:create_app --port 7484
```

```sh
curl -H "X-Example-Key: change-me" http://127.0.0.1:7484/api/projects/demo/simulator          # status
curl -X POST -H "X-Example-Key: change-me" http://127.0.0.1:7484/api/projects/demo/simulator  # start, and a ticket
curl -H "X-Example-Key: change-me" -H "X-Example-Project: demo" http://127.0.0.1:7484/api/agent/simulator/manifest
```

## What a host decides

| Seam | Here | A real host |
|---|---|---|
| `ConfigSource` | SimMirror's `embedded` defaults, simulator on | its settings, read on every call so a change applies at once |
| `StateStore` | `~/.sim-mirror-example`, claims shared with every host | its own folders; keep the run folder's path short (unix sockets) |
| `Policy` | every project, no commands | who may have a simulator, run builds, install from where |
| `Authenticator` | a shared key header | its own sign-in; `Refused(status, message)` when not |
| `HostCopy` | SimMirror's wording | its own words for where settings are |

## Giving agents the tools

An MCP client talks to the agent routes through SimMirror's relay, which reads the URL and each credential from the
environment, never from its command line:

```python
from sim_mirror.api import relay_command

argv = relay_command(
    url_env="EXAMPLE_TOOLS_URL",  # http://127.0.0.1:7484/api/agent/simulator
    headers={"X-Example-Key": "EXAMPLE_KEY", "X-Example-Project": "EXAMPLE_PROJECT"},
    client_header="X-Example-Client",
    server_name="simulator",
)
```

Put `argv` in the client's MCP configuration and the three variables in its environment.

## Before you ship it

This example leaves out what a real deployment needs in front of the routers: checking the `Host` and `Origin` of
state-changing requests and WebSockets, CORS for exactly the origins you serve, and a Content-Security-Policy. The
standalone daemon does all of these; see its security notes.
