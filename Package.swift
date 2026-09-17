// swift-tools-version:6.0
// SPDX-License-Identifier: Apache-2.0
//
// SimMirrorKit: SimMirror's optional debug SDK. An app under development links it, calls `SimMirror.start()`, and
// SimMirror reads the app's own UIKit and SwiftUI view hierarchy -- so a screen whose controls have no accessibility
// labels still reads well. It does nothing outside a Debug build on the iOS Simulator. See docs/app-sdk.md.
//
// This manifest is at the top of the repository because SwiftPM needs it there for a package added by URL. SimMirror's
// native helper is a separate package in helper/.

import PackageDescription

let package = Package(
    name: "SimMirror",
    platforms: [.iOS(.v16)],
    products: [
        .library(name: "SimMirrorKit", targets: ["SimMirrorKit"])
    ],
    targets: [
        .target(name: "SimMirrorKit", path: "sdk/swift/Sources/SimMirrorKit"),
        .testTarget(
            name: "SimMirrorKitTests",
            dependencies: ["SimMirrorKit"],
            path: "sdk/swift/Tests/SimMirrorKitTests"
        ),
    ]
)
