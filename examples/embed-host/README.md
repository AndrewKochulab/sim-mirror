# Embed host

The backend a page showing SimMirror needs, in one standard-library file: [host.py](host.py). The
[iframe](../iframe/) and [web-component](../web-component/) examples both run on it.

It does three things, and keeps the one secret:

- serves the page's folder;
- answers `POST /api/embed-ticket` with a **one-shot embed ticket** it gets from the daemon with its viewer token --
  the page never sees the token, and a ticket opens the viewer once, within a minute;
- serves the viewer library at `/sim-mirror.js`, from `viewer/dist/` once it is built.

```sh
sim-mirror serve --detach
sim-mirror token create --kind viewer --scope demo --label "example page"
SIM_MIRROR_TOKEN=<the token it printed> python3 examples/embed-host/host.py --page examples/iframe
```

Options: `--scope` (default `demo`), `--server` (default `http://127.0.0.1:7466`), `--port` (default `7483`).

A real application does the same from its own backend, after its own sign-in: mint the ticket per page view, and never
put the SimMirror token in the page. An application that also starts agents, or shares the daemon with others, uses a
host token and `DaemonHost` instead: see [Sharing the daemon between hosts](../../docs/embedding/shared-daemon.md).
