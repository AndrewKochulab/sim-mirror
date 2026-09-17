# SPDX-License-Identifier: Apache-2.0
"""The app SDK protocol's fixed parts, as ``protocol/app-sdk/v1`` defines them, and the limits it is read within.

An app is code SimMirror did not write, so everything it says is read within a size, a depth and a count, and a
listing is only trusted as far as its owner and permissions go (`discovery`).
"""

from __future__ import annotations

#: The app SDK protocol version SimMirror speaks. An app speaking a newer one is still read, and the snapshot says so.
PROTOCOL_VERSION = 1
#: What an element an app shared is marked as: the reader's name.
SOURCE = "app"
#: Where, under a simulator's data folder or an app's own container, a running app writes its listing.
LISTINGS = ("Library", "Caches", "SimMirror", "apps")
#: Where an app's containers are, under a simulator's data folder.
CONTAINERS = ("Containers", "Data", "Application")
HIERARCHY_PATH = "/v1/hierarchy"
#: The only address an app listens on, and the only one SimMirror asks.
HOST = "127.0.0.1"

LISTING_MAX_BYTES = 16 * 1024
HEADERS_MAX_BYTES = 16 * 1024
RESPONSE_MAX_BYTES = 8 * 1024 * 1024
#: How deep a hierarchy is read: deeper views are left out, and the hierarchy counts as cut short.
DEPTH_MAX = 128
#: How many listings a simulator's data folder is read for; the newest first.
LISTINGS_MAX = 64
#: How many apps are asked at once for their hierarchy.
ASKED_MAX = 8
#: How long an app that did not answer in time is left alone, unless it writes its listing again.
SUSPENDED_S = 10.0
#: How much of an app's name and its notes a snapshot keeps.
NAME_MAX = 100
NOTE_MAX = 200
NOTES_MAX = 5
