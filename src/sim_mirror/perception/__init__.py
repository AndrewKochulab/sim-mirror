# SPDX-License-Identifier: Apache-2.0
"""How SimMirror understands what is on a device's screen, in as few tokens as an agent can act on.

* `model` -- the screen as a tree of elements, whichever reader found them;
* `readers` -- where trees come from: the idb connector's accessibility document today, and more readers merged in;
* `snapshot` -- the tree as a few lines with stable refs, a digest, and a diff between two screens;
* `wait` and `settle` -- waiting for text to appear or go, or for the screen to stop moving;
* `budget` -- an estimate of what an answer costs an agent, in tokens.
"""
