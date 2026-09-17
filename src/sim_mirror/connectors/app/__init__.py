# SPDX-License-Identifier: Apache-2.0
"""An app's own view hierarchy, shared by a debug build with SimMirror's SDK and merged into snapshots.

Accessibility reads a screen well when its controls have labels; an icon button, a tappable card or a custom control
without one reads as nothing. An app built with SimMirror's debug SDK (``SimMirrorKit``) hands over its UIKit and
SwiftUI views instead: it listens on loopback and says where in a listing (`discovery`), answers the protocol in
``protocol/app-sdk/v1`` (`wire`, `client`), and its answer is read in the shape a connector's accessibility document
has (`document`). `reader` finds the app in front, and `merge` adds what it says to an agent's snapshots -- naming what
accessibility found unlabeled -- for the scopes whose ``connectors.app.merge`` is on.

It is not a connector: it drives nothing, and only adds to what another connector reads.
"""
