# iframe

A page on another origin showing the viewer in a frame. The daemon only lets pages it was told of frame it, so first
add this page's origin to SimMirror's config (`sim-mirror config path` shows the file):

```toml
[security]
frame_ancestors = ["http://127.0.0.1:7483"]
```

Then, with the daemon started after that change:

```sh
sim-mirror token create --kind viewer --scope demo --label "iframe example"
SIM_MIRROR_TOKEN=<the token it printed> python3 examples/embed-host/host.py --page examples/iframe
```

and open <http://127.0.0.1:7483/>.

How it works: the page asks its own backend ([embed-host](../embed-host/)) for a ticket; the backend asks the daemon
with its viewer token, and answers with `http://127.0.0.1:7466/embed/demo#ticket=…`. The embed page takes the ticket
out of its address, spends it for a viewer token it keeps in memory, and shows the device. The ticket works once, so a
reload of the frame alone says to reload the page.

The same page served from an origin not in `frame_ancestors` gets a blank frame: the browser refuses it, and says so
in the console.
