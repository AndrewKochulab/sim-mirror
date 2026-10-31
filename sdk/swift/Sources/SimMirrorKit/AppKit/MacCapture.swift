// SPDX-License-Identifier: Apache-2.0
#if DEBUG && os(macOS)
    import AppKit
    import Foundation

    /// Reads only views and accessibility objects owned by this process. Frames use the selected window's top left.
    @MainActor
    struct MacCapture {
        var options: SimMirror.Options
        var osMajor = ProcessInfo.processInfo.operatingSystemVersion.majorVersion

        func capture(_ window: NSWindow, limit: Int) -> Hierarchy {
            let began = ProcessInfo.processInfo.systemUptime
            var budget = NodeBudget(limit: min(options.maxNodes, limit))
            var hostings: [(any MacDebugHosting, CGRect)] = []
            var tags: [(Tag, CGRect)] = []
            var frames: [CGRect] = []
            var seen = Set<ObjectIdentifier>()
            func walk(_ object: AnyObject, clip: CGRect) -> Node? {
                guard !(object is NSCell) else { return nil }
                guard seen.insert(ObjectIdentifier(object)).inserted, budget.take() else { return nil }
                let view = object as? NSView
                guard let ax = MacAccess(object) else { return nil }
                var rect: CGRect
                if let view {
                    guard view is TagAnchorView || !view.isHiddenOrHasHiddenAncestor else { return nil }
                    let local = view.convert(view.bounds, to: nil)
                    rect = CGRect(
                        x: local.minX, y: window.frame.height - local.maxY, width: local.width, height: local.height)
                } else if let screen = ax.frame {
                    rect = CGRect(
                        x: screen.minX - window.frame.minX, y: window.frame.maxY - screen.maxY, width: screen.width,
                        height: screen.height)
                } else {
                    return nil
                }
                rect = rect.intersection(clip)
                guard !rect.isNull, !rect.isEmpty else { return nil }
                frames.append(rect)
                if let host = object as? any MacDebugHosting { hostings.append((host, rect)) }
                if let anchor = object as? TagAnchorView { tags.append((anchor.mirrorTag, rect)); return nil }
                let role = ax.role
                let field = view as? NSTextField
                let button = view as? NSButton
                let kind: SimMirror.Kind
                let secure =
                    field is NSSecureTextField || ax.subrole == "AXSecureTextField"
                if secure {
                    kind = .secure
                } else if role == "AXStaticText" || (field != nil && field?.isEditable == false) {
                    kind = .text
                } else if role == "AXCheckBox" || role == "AXSwitch" || view is NSSwitch {
                    kind = .switch
                } else if field != nil || role == "AXTextField" || role == "AXTextArea" {
                    kind = .field
                } else if button != nil || role == "AXButton" {
                    kind = .button
                } else if view is NSScrollView || role == "AXScrollArea" {
                    kind = .scroll
                } else if view is NSTableView || role == "AXList" {
                    kind = .list
                } else if role == "AXLink" {
                    kind = .link
                } else {
                    kind = .container
                }
                var value: String?
                if !options.redactValues, !secure {
                    value =
                        field?.stringValue ?? ax.text
                }
                var objects = view?.subviews.map { $0 as AnyObject } ?? []
                objects += ax.children
                // Field editors and cells must not expose a secure field's contents.
                if kind.isControl || kind == .text { objects = [] }
                return Node(
                    kind: kind,
                    label: ax.label ?? button?.title
                        ?? (kind == .text ? field?.stringValue ?? value : field?.placeholderString),
                    labelSource: .accessibility, identifier: ax.identifier, value: value,
                    placeholder: field?.placeholderString, frame: Frame(rect),
                    enabled: ax.enabled,
                    interactive: kind.isControl, source: .appkit,
                    typeName: String(describing: type(of: object)),
                    children: objects.compactMap { walk($0, clip: rect) })
            }
            var nodes =
                window.contentView.flatMap {
                    walk($0, clip: CGRect(origin: .zero, size: window.frame.size))
                }.map { [$0] } ?? []
            nodes = TagMerger.merge(tags, into: nodes)
            var notes: [String] = []
            if options.swiftUIDebugData {
                if osMajor == 26 {
                    for (host, bounds) in hostings {
                        switch DebugDataDecoder.decode(host.serializedDebugData()) {
                        case .nodes(let roots):
                            nodes = DebugFindingMerger.merge(
                                DebugDataPlacer.findings(in: roots, platformViews: frames, within: bounds), into: nodes)
                        case .empty:
                            notes.append("SwiftUI recorded no debug data; start the SDK before constructing views")
                        case .unsupported:
                            notes.append("SwiftUI debug data could not be read; using AppKit and accessibility")
                        }
                    }
                } else {
                    notes.append("macOS SwiftUI debug data is supported on macOS 26 only")
                }
            }
            return Hierarchy(
                protocolVersion: WireProtocol.version, sdkVersion: SimMirror.sdkVersion,
                app: AppInfo(
                    bundleID: Bundle.main.bundleIdentifier ?? "", name: ProcessInfo.processInfo.processName,
                    pid: getpid(), active: NSApp.isActive),
                screen: ScreenInfo(
                    widthPt: window.frame.width, heightPt: window.frame.height,
                    scale: window.backingScaleFactor, orientation: .portrait),
                modal: window.sheetParent == nil ? nil : Modal(kind: .sheet, name: window.title), keyboard: nil,
                windows: [Window(level: Double(window.level.rawValue), key: window.isKeyWindow, nodes: nodes)],
                truncated: budget.exhausted, nodeCount: nodes.reduce(0) { $0 + $1.count },
                captureMs: (ProcessInfo.processInfo.systemUptime - began) * 1000,
                notes: notes)
        }
    }
#endif
