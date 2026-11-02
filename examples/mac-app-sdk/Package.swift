// swift-tools-version:6.0
// SPDX-License-Identifier: Apache-2.0
import PackageDescription

let package = Package(
    name: "MacAppSDK",
    platforms: [.macOS(.v14)],
    dependencies: [.package(name: "SimMirror", path: "../..")],
    targets: [
        .executableTarget(
            name: "MacAppSDK", dependencies: [.product(name: "SimMirrorKit", package: "SimMirror")], path: "Sources")
    ]
)
