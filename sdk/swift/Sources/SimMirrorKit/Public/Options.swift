// SPDX-License-Identifier: Apache-2.0
extension SimMirror {
    /// How the app answers SimMirror.
    public struct Options: Sendable, Equatable {
        /// The port to listen on, on 127.0.0.1 only. 0, the default, lets the system pick a free one each launch.
        public var port: UInt16

        /// The most views one answer holds; SimMirror may ask for fewer. Views past it are left out and the answer
        /// says it was cut short.
        public var maxNodes: Int

        /// Leaves every value out -- a field's text, a slider's position -- for screens that show data you would
        /// rather not hand an agent. A secure field's text is never read either way.
        public var redactValues: Bool

        /// Also reads SwiftUI's debug data to find views with a tap gesture and name images. Off by default: it slows
        /// the first answer, works on iOS 26 only, and needs `start` to be called before any SwiftUI view is built.
        public var swiftUIDebugData: Bool

        /// Options for `SimMirror.start(_:)`.
        public init(port: UInt16 = 0, maxNodes: Int = 3000, redactValues: Bool = false, swiftUIDebugData: Bool = false)
        {
            self.port = port
            self.maxNodes = maxNodes
            self.redactValues = redactValues
            self.swiftUIDebugData = swiftUIDebugData
        }
    }
}
