// SPDX-License-Identifier: Apache-2.0
#if DEBUG && os(macOS)
    import AppKit
    import SwiftUI

    /// Isolates SwiftUI's underscored debugging API; release builds never reference it.
    @MainActor
    protocol MacDebugHosting: NSView {
        func serializedDebugData() -> Data?
    }
    extension NSHostingView: MacDebugHosting {
        func serializedDebugData() -> Data? { _ViewDebug.serializedData(_viewDebugData()) }
    }
#endif
