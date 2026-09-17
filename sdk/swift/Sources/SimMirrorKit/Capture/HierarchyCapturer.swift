// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import UIKit

    /// Captures what the app shows: its windows from the front, what a modal leaves reachable, and where the keyboard is.
    @MainActor
    struct HierarchyCapturer {
        let app: AppInfo
        let describers: DescriberRegistry
        let redactValues: Bool
        let debugData: DebugDataPolicy
        let clock: () -> TimeInterval

        init(
            app: AppInfo,
            describers: DescriberRegistry,
            redactValues: Bool = false,
            debugData: DebugDataPolicy = .off,
            clock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }
        ) {
            self.app = app
            self.describers = describers
            self.redactValues = redactValues
            self.debugData = debugData
            self.clock = clock
        }

        func capture(windows: [UIWindow], keyboard: CGRect?, maxNodes: Int) -> Hierarchy {
            let started = clock()
            let ordered = Self.ordered(windows)
            let screen = ordered.first?.windowScene?.screen ?? UIScreen.main
            let space = screen.fixedCoordinateSpace
            var budget = NodeBudget(limit: maxNodes)
            var notes: [String] = []
            if case .refused(let reason) = debugData { notes.append(reason) }
            var modal: Modal?
            var cut = false
            var captured: [Window] = []
            for window in ordered {
                let found = ModalDetector.detect(in: window)
                var walker = UIKitWalker(
                    describers: describers, redactValues: redactValues, space: space, budget: budget,
                    isHosting: debugData == .on ? DebugDataReader.isHosting : { _ in false }
                )
                let root = found?.view ?? window
                let clip = window.convert(window.bounds, to: space)
                var nodes = walker.walk(root, clip: clip)
                nodes = TagMerger.merge(walker.tags, into: nodes)
                if debugData == .on {
                    let read = DebugDataReader.read(walker.hostings)
                    nodes = DebugFindingMerger.merge(read.findings, into: nodes)
                    notes += read.notes.filter { !notes.contains($0) }
                }
                budget = walker.budget
                cut = cut || walker.cut || budget.exhausted
                captured.append(
                    Window(level: Double(window.windowLevel.rawValue), key: window.isKeyWindow, nodes: nodes))
                if let found {
                    modal = found.modal
                    break
                }
            }
            var app = app
            app.active = true
            let bounds = space.bounds
            return Hierarchy(
                protocolVersion: WireProtocol.version,
                sdkVersion: SimMirror.sdkVersion,
                app: app,
                screen: ScreenInfo(
                    widthPt: Double(bounds.width), heightPt: Double(bounds.height), scale: Double(screen.scale),
                    orientation: Self.orientation(ordered.first?.windowScene?.interfaceOrientation ?? .unknown)
                ),
                modal: modal,
                keyboard: keyboard.map { Keyboard(frame: Frame($0)) },
                windows: captured,
                truncated: cut,
                nodeCount: captured.reduce(0) { total, window in total + window.nodes.reduce(0) { $0 + $1.count } },
                captureMs: Frame.rounded((clock() - started) * 1000),
                notes: notes
            )
        }

        /// The windows worth reading, the key window first, then from the front to the back. The keyboard's own
        /// windows are left out: where the keyboard is is said once, as the keyboard.
        static func ordered(_ windows: [UIWindow]) -> [UIWindow] {
            let shown = windows.enumerated().filter { _, window in
                let name = NSStringFromClass(type(of: window))
                return !window.isHidden && window.alpha >= 0.01 && !name.contains("Keyboard")
                    && !name.contains("TextEffects")
            }
            return shown.sorted { first, second in
                if first.element.isKeyWindow != second.element.isKeyWindow { return first.element.isKeyWindow }
                if first.element.windowLevel != second.element.windowLevel {
                    return first.element.windowLevel > second.element.windowLevel
                }
                return first.offset > second.offset
            }.map(\.element)
        }

        static func orientation(_ orientation: UIInterfaceOrientation) -> Orientation {
            switch orientation {
            case .portrait: .portrait
            case .portraitUpsideDown: .portraitUpsideDown
            case .landscapeLeft: .landscapeLeft
            case .landscapeRight: .landscapeRight
            default: .unknown
            }
        }
    }
#endif
