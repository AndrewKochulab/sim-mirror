# SPDX-License-Identifier: Apache-2.0
"""How SimMirror reaches a device: connectors (`base`), chosen by what they can do here (`registry`).

Built in: `idb` -- idb_companion, for the whole screen, touches, keys and the accessibility tree -- and `simctl`, which
needs only Xcode and can show the screen but not touch it. Others are installed as packages that register in the
``sim_mirror.connectors`` entry-point group (`docs/contributing/connector-guide.md`).
"""
