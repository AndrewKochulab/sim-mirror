// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import SwiftUI
    import UIKit

    /// A view SwiftUI's debug data can be read from. This file is the only one that names SwiftUI's underscored
    /// hosting view: if a release of SwiftUI changes it, this is where SimMirrorKit changes.
    @MainActor
    protocol DebugDataHosting: UIView {
        func serializedDebugData() -> Data?
    }

    extension _UIHostingView: DebugDataHosting {
        func serializedDebugData() -> Data? {
            _ViewDebug.serializedData(_viewDebugData())
        }
    }

    /// Reads the debug data of the hosting views a walk met, and says what they show.
    @MainActor
    struct DebugDataReader {
        static let unsupported =
            "SwiftUI's debug data has a shape this SDK does not know; SwiftUI views were read from UIKit"
        static let empty =
            "SwiftUI recorded no debug data: call SimMirror.start(_:) in your App's init, before any view is built"

        static func isHosting(_ view: UIView) -> Bool {
            view is any DebugDataHosting
        }

        /// What the hosting views show, and notes on those that could not be read.
        static func read(_ hostings: [HostingFound]) -> (findings: [DebugFinding], notes: [String]) {
            var findings: [DebugFinding] = []
            var notes: [String] = []
            for hosting in hostings {
                guard let host = hosting.view as? any DebugDataHosting else { continue }
                switch DebugDataDecoder.decode(host.serializedDebugData()) {
                case .nodes(let roots):
                    findings += DebugDataPlacer.findings(
                        in: roots, platformViews: hosting.platformViews, within: hosting.frame)
                case .empty:
                    notes.append(empty)
                case .unsupported:
                    notes.append(unsupported)
                }
            }
            var unique: [String] = []
            for note in notes where !unique.contains(note) { unique.append(note) }
            return (findings, unique)
        }
    }
#endif
