// swift-tools-version:6.0
// SPDX-License-Identifier: Apache-2.0
//
// SimMirror's native helper: the screen, input and element tree of one booted simulator, served to SimMirror on a unix
// socket -- without idb_companion.
//
// `HelperCore` is everything that can be decided without a simulator: the wire protocol, what a screenshot or a stream
// is cut to, how input maps onto each HID transport, and how an accessibility tree becomes SimMirror's document. It is
// unit-tested to the per-file coverage bar. `HelperPlatform` is the thin layer that reaches Apple's private simulator
// frameworks through the Objective-C runtime; it is exercised against real simulators by SimMirror's live suite.

import PackageDescription

let package = Package(
    name: "SimMirrorHelper",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "sim-mirror-helper", targets: ["sim-mirror-helper"]),
    ],
    targets: [
        .target(name: "HelperCore"),
        .target(name: "ObjCGuard"),
        .target(
            name: "HelperPlatform",
            dependencies: ["HelperCore", "ObjCGuard"],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .executableTarget(
            name: "sim-mirror-helper",
            dependencies: ["HelperCore", "HelperPlatform"],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(name: "HelperCoreTests", dependencies: ["HelperCore"]),
    ]
)
