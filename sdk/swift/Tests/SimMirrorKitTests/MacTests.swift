// SPDX-License-Identifier: Apache-2.0
#if os(macOS)
    import AppKit
    import Foundation
    import Testing
    import SwiftUI
    @testable import SimMirrorKit

    @MainActor
    final class MacTestWindow: NSWindow {
        var shown = true
        override var isVisible: Bool { shown }
    }
    @MainActor
    final class MacTestTarget: NSObject {
        var calls = 0
        @objc func call() { calls += 1 }
    }
    @MainActor
    final class PlainAccessible: NSObject {
        var role = "AXButton"
        @objc func accessibilityRole() -> String { role }
        @objc func accessibilitySubrole() -> String { "" }
        @objc func accessibilityLabel() -> String { "Save" }
        @objc func accessibilityIdentifier() -> String { "save" }
        @objc func accessibilityValue() -> NSNumber { 1 }
        @objc func accessibilityFrame() -> CGRect { CGRect(x: 1, y: 2, width: 30, height: 20) }
        @objc func accessibilityChildren() -> [Any] { [] }
        @objc func isAccessibilityEnabled() -> Bool { true }
        @objc func accessibilityPerformPress() -> Bool { true }
    }

    @MainActor
    final class MacEventView: NSView {
        var events: [NSEvent.EventType] = []
        override func mouseDown(with event: NSEvent) { events.append(event.type) }
        override func mouseUp(with event: NSEvent) { events.append(event.type) }
        override func mouseMoved(with event: NSEvent) { events.append(event.type) }
    }
    @MainActor
    final class MacTextView: NSTextView {
        var keys: [NSEvent.ModifierFlags] = []
        var captured = ""
        var replaceNext = false
        var allowsSelection = true
        override func tryToPerform(_ action: Selector, with object: Any?) -> Bool {
            allowsSelection && super.tryToPerform(action, with: object)
        }
        override func becomeFirstResponder() -> Bool { true }
        override func resignFirstResponder() -> Bool { true }
        override func selectAll(_ sender: Any?) { replaceNext = true }
        override func insertText(_ text: Any, replacementRange: NSRange) {
            captured = (replaceNext ? "" : captured) + (text as? String ?? "")
            replaceNext = false
        }
        override func keyDown(with event: NSEvent) { keys.append(event.modifierFlags) }
    }
    @MainActor
    final class MacDebugView: NSView, MacDebugHosting {
        var data: Data?
        func serializedDebugData() -> Data? { data }
    }

    @Suite(.serialized)
    @MainActor
    struct MacTests {
        init() { _ = NSApplication.shared }
        func window() -> MacTestWindow {
            let window = MacTestWindow(
                contentRect: CGRect(x: 100, y: 100, width: 400, height: 400),
                styleMask: .borderless, backing: .buffered, defer: true)
            window.isReleasedWhenClosed = false
            return window
        }
        func all(_ nodes: [Node]) -> [Node] { nodes.flatMap { [$0] + all($0.children) } }

        @Test func accessibilitySelectorsWorkWithoutDeclaredProtocolConformance() throws {
            let ax = try #require(MacAccess(PlainAccessible()))
            #expect(ax.role == "AXButton" && ax.subrole == "" && ax.label == "Save")
            #expect(ax.identifier == "save" && ax.text == "1" && ax.enabled && ax.press())
            #expect(ax.frame == CGRect(x: 1, y: 2, width: 30, height: 20) && ax.children.isEmpty)
            let absent = try #require(MacAccess(NSObject()))
            #expect(absent.value("missingSelector") == nil && absent.flag("missingSelector") == nil)
            #expect(absent.frame == nil && absent.children.isEmpty && absent.role == "" && absent.subrole == "")
            #expect(absent.label == nil && absent.identifier == nil && absent.text == nil && absent.enabled)
            #expect(!absent.press() && MacAccess(nil) == nil)
        }

        @Test func captureReadsControlsAndRedactsSecureChildrenAndBoundsTheWalk() throws {
            let window = window()
            let button = NSButton(title: "Save", target: nil, action: nil)
            button.setAccessibilityIdentifier("save")
            let field = NSTextField(string: "Name")
            field.placeholderString = "Name"
            let secure = NSSecureTextField(string: "private")
            let toggle = NSSwitch()
            let text = NSTextField(labelWithString: "Heading")
            let scroll = NSScrollView()
            let list = NSTableView()
            for (index, view) in [button, field, secure, toggle, text, scroll, list].enumerated() {
                view.frame = CGRect(x: 10, y: 10 + index * 40, width: 100, height: 30)
                window.contentView?.addSubview(view)
            }
            let link = PlainAccessible()
            link.role = "AXLink"
            window.contentView?.setAccessibilityChildren([NSObject(), link])
            let document = MacCapture(options: .init()).capture(window, limit: 100)
            let nodes = all(document.windows.flatMap(\.nodes))
            #expect(nodes.contains { $0.identifier == "save" && $0.kind == .button })
            #expect(nodes.contains { $0.kind == .field && $0.value == "Name" })
            #expect(nodes.contains { $0.kind == .secure && $0.value == nil && $0.children.isEmpty })
            #expect(nodes.contains { $0.kind == .switch } && nodes.contains { $0.kind == .scroll })
            #expect(!String(decoding: WireJSON.encode(document), as: UTF8.self).contains("private"))
            #expect(
                all(MacCapture(options: .init(redactValues: true)).capture(window, limit: 100).windows.flatMap(\.nodes))
                    .allSatisfy { $0.value == nil })
            #expect(MacCapture(options: .init()).capture(window, limit: 1).truncated)
        }

        @Test func captureClipsScrolledViewsAndDoesNotRepeatCells() throws {
            let window = window()
            let scroll = NSScrollView(frame: CGRect(x: 20, y: 20, width: 180, height: 100))
            let document = NSView(frame: CGRect(x: 0, y: 0, width: 180, height: 500))
            let visible = NSTextField(labelWithString: "Visible row")
            visible.frame = CGRect(x: 10, y: 10, width: 120, height: 20)
            let outside = NSTextField(labelWithString: "Outside row")
            outside.frame = CGRect(x: 10, y: 300, width: 120, height: 20)
            let hidden = NSTextField(labelWithString: "Hidden row")
            hidden.frame = visible.frame
            hidden.isHidden = true
            for field in [visible, outside, hidden] { document.addSubview(field) }
            scroll.documentView = document
            window.contentView?.addSubview(scroll)
            scroll.contentView.scroll(to: .zero)
            let nodes = all(MacCapture(options: .init()).capture(window, limit: 100).windows.flatMap(\.nodes))
            #expect(nodes.filter { $0.label == "Visible row" }.count == 1)
            #expect(!nodes.contains { $0.label == "Outside row" || $0.label == "Hidden row" })
            #expect(!nodes.contains { $0.typeName.contains("Cell") })
        }

        @Test func inputCallsOnlyTheSelectedWindowsControlsAndMenus() throws {
            let window = window()
            let target = MacTestTarget()
            let button = NSButton(title: "Save", target: target, action: #selector(MacTestTarget.call))
            button.frame = CGRect(x: 20, y: 300, width: 100, height: 40)
            window.contentView?.addSubview(button)
            try MacInput.perform(
                MacCommand(kind: "tap", windowID: window.windowNumber, x: 50, y: 80), windows: [window])
            #expect(target.calls == 1)
            let original = NSApp.mainMenu
            defer { NSApp.mainMenu = original }
            let menu = NSMenu()
            let root = NSMenuItem(title: "Sample", action: nil, keyEquivalent: "")
            let child = NSMenu()
            let item = NSMenuItem(title: "Save", action: #selector(MacTestTarget.call), keyEquivalent: "")
            item.target = target
            child.addItem(item); root.submenu = child; menu.addItem(root); NSApp.mainMenu = menu
            try MacInput.perform(
                MacCommand(kind: "menu", windowID: window.windowNumber, path: ["Sample", "Save"]), windows: [window])
            #expect(target.calls == 2)
            let field = NSTextField(string: "Name")
            field.frame = CGRect(x: 20, y: 200, width: 100, height: 30)
            window.contentView?.addSubview(field)
            try MacInput.perform(
                MacCommand(kind: "tap", windowID: window.windowNumber, x: 50, y: 185), windows: [window])
            #expect(window.firstResponder != nil)
            for path in [["Sample"], ["Missing"], ["Sample", "Save", "Other"]] {
                #expect(throws: MacInputFailure.self) {
                    try MacInput.perform(
                        MacCommand(kind: "menu", windowID: window.windowNumber, path: path), windows: [window])
                }
            }
            window.contentView = TagAnchorView(tag: Tag())
            #expect(throws: MacInputFailure.self) {
                try MacInput.perform(
                    MacCommand(kind: "tap", windowID: window.windowNumber, x: 10, y: 10), windows: [window])
            }
        }

        @Test func malformedOrUnsupportedInputNeverGetsAFallbackOutsideTheApp() {
            let window = window()
            let commands = [
                MacCommand(kind: "tap", windowID: -999, x: 10, y: 10),
                MacCommand(kind: "tap", windowID: window.windowNumber),
                MacCommand(kind: "tap", windowID: window.windowNumber, x: -1, y: 10),
                MacCommand(kind: "tap", windowID: window.windowNumber, x: 401, y: 10),
                MacCommand(kind: "right_click", windowID: window.windowNumber, x: 10, y: 10),
                MacCommand(kind: "scroll", windowID: window.windowNumber, x: 10, y: 10),
                MacCommand(kind: "scroll", windowID: window.windowNumber, x: 10, y: 10, dx: 1, dy: 2),
                MacCommand(kind: "type", windowID: window.windowNumber, text: "hello"),
                MacCommand(kind: "keys", windowID: window.windowNumber),
                MacCommand(kind: "keys", windowID: window.windowNumber, text: "unknown+key"),
                MacCommand(kind: "keys", windowID: window.windowNumber, text: "cmd+tab"),
                MacCommand(kind: "menu", windowID: window.windowNumber, path: ["Apple"]),
                MacCommand(kind: "other", windowID: window.windowNumber),
            ]
            for command in commands {
                #expect(throws: MacInputFailure.self) { try MacInput.perform(command, windows: [window]) }
            }
            window.shown = false
            #expect(throws: MacInputFailure.self) {
                try MacInput.perform(
                    MacCommand(kind: "keys", windowID: window.windowNumber, text: "return"), windows: [window])
            }
        }

        @Test func responseValidationAndTimeoutCancellationDoNotApplyLateInput() {
            let window = window()
            let options = SimMirror.Options()
            func request(_ method: String, _ path: String, query: [String: String] = [:], body: Data = Data())
                -> HTTPRequest
            {
                HTTPRequest(method: method, path: path, query: query, headers: [:], body: body)
            }
            #expect(
                MacRuntime.respond(request("GET", "/v1/hierarchy"), options: options, windows: [window]).status == 200)
            #expect(
                MacRuntime.respond(
                    request("GET", "/v1/hierarchy", query: ["window_id": "-999"]), options: options, windows: [window]
                ).status == 400)
            #expect(
                MacRuntime.respond(
                    request("GET", "/v1/hierarchy", query: ["max_nodes": "0"]), options: options, windows: [window]
                ).status == 400)
            #expect(
                MacRuntime.respond(
                    request(
                        "GET", "/v1/hierarchy", query: ["window_id": String(window.windowNumber), "max_nodes": "1"]),
                    options: options,
                    windows: [window]
                ).status == 200)
            #expect(MacRuntime.respond(request("GET", "/other"), options: options, windows: []).status == 404)
            #expect(
                MacRuntime.respond(
                    request("POST", "/v1/input", body: Data("{}".utf8)), options: options, windows: [window]
                ).status == 400)
            #expect(
                MacRuntime.respond(
                    request(
                        "POST", "/v1/input",
                        body: WireJSON.encode(MacCommand(kind: "invalid", windowID: window.windowNumber))),
                    options: options, windows: [window]
                ).status == 400)
            let done = MacReply()
            done.finish { HTTPResponse(status: 200, body: Data()) }
            #expect(done.take()?.status == 200)
            let cancelled = MacReply()
            #expect(cancelled.take() == nil)
            var changed = false
            cancelled.finish {
                changed = true; return HTTPResponse(status: 200, body: Data())
            }
            #expect(!changed)
        }

        @Test func customEventsScrollingAndTextStayInOwnedViews() throws {
            let window = window()
            let custom = MacEventView(frame: CGRect(x: 0, y: 0, width: 400, height: 400))
            window.contentView = custom
            try MacInput.perform(
                MacCommand(kind: "tap", windowID: window.windowNumber, x: 40, y: 40), windows: [window],
                enqueue: { #expect($0.type == .leftMouseUp) })
            try MacInput.perform(
                MacCommand(kind: "hover", windowID: window.windowNumber, x: 40, y: 40), windows: [window],
                enqueue: { #expect($0.type == .leftMouseUp) })
            #expect(custom.events == [.leftMouseDown, .leftMouseUp, .mouseMoved])
            let scroll = NSScrollView(frame: custom.bounds)
            let document = NSView(frame: CGRect(x: 0, y: 0, width: 800, height: 800))
            scroll.documentView = document
            window.contentView = scroll
            try MacInput.perform(
                MacCommand(kind: "scroll", windowID: window.windowNumber, x: 40, y: 40, dx: 20, dy: -30),
                windows: [window])
            #expect(scroll.contentView.bounds.origin != .zero)
            let text = MacTextView(frame: custom.bounds)
            window.contentView = text
            #expect(window.makeFirstResponder(text))
            try MacInput.perform(
                MacCommand(kind: "type", windowID: window.windowNumber, text: "Hello"), windows: [window])
            try MacInput.perform(
                MacCommand(kind: "type", windowID: window.windowNumber, text: "New", clear: true, submit: true),
                windows: [window])
            #expect(text.captured == "New" && text.keys.count == 1)
            try MacInput.perform(
                MacCommand(kind: "keys", windowID: window.windowNumber, text: "cmd+shift+alt+ctrl+x"), windows: [window]
            )
            #expect(text.keys.last?.contains([.command, .shift, .option, .control]) == true)
            let success = MacRuntime.respond(
                HTTPRequest(
                    method: "POST", path: "/v1/input", query: [:], headers: [:],
                    body: WireJSON.encode(MacCommand(kind: "type", windowID: window.windowNumber, text: "!"))),
                options: .init(), windows: [window])
            #expect(success.status == 200 && text.captured == "New!")
            text.allowsSelection = false
            #expect(throws: MacInputFailure.self) {
                try MacInput.perform(
                    MacCommand(kind: "type", windowID: window.windowNumber, text: "Lost", clear: true),
                    windows: [window])
            }
            #expect(text.captured == "New!")
        }

        struct Tagged: View {
            let name: String
            var body: some View {
                VStack {
                    Color.red.frame(width: 60, height: 40).simMirror(kind: .button, name: name, identifier: "drawn")
                    Text("Named").simMirror("Name")
                }
            }
        }
        @Test func hostingTagsAndOptionalDebugDataRemainReadable() throws {
            let window = window()
            let host = NSHostingView(rootView: Tagged(name: "Drawing"))
            window.contentView = host
            host.layoutSubtreeIfNeeded()
            func anchors(_ view: NSView) -> [TagAnchorView] {
                (view as? TagAnchorView).map { [$0] } ?? view.subviews.flatMap(anchors)
            }
            let anchor = try #require(anchors(host).first)
            #expect(anchor.hitTest(.zero) == nil)
            let initial = MacCapture(options: .init()).capture(window, limit: 100)
            #expect(
                all(initial.windows.flatMap(\.nodes)).contains { $0.identifier == "drawn" && $0.label == "Drawing" })
            host.rootView = Tagged(name: "Updated")
            host.layoutSubtreeIfNeeded()
            #expect(anchors(host).contains { $0.mirrorTag.name == "Updated" })
            _ = host.serializedDebugData()
            let debug = MacDebugView(frame: host.bounds)
            window.contentView = debug
            let options = SimMirror.Options(swiftUIDebugData: true)
            #expect(!MacCapture(options: options, osMajor: 25).capture(window, limit: 100).notes.isEmpty)
            #expect(!MacCapture(options: options, osMajor: 26).capture(window, limit: 100).notes.isEmpty)
            debug.data = try DebugDataTests.data([
                DebugDataTests.raw("Text", at: [10, 10], size: [30, 20], value: DebugDataTests.text("Debug label"))
            ])
            _ = MacCapture(options: options, osMajor: 26).capture(window, limit: 100)
            debug.data = Data("[]".utf8)
            #expect(!MacCapture(options: options, osMajor: 26).capture(window, limit: 100).notes.isEmpty)
        }

        @Test func runtimeOwnsItsPrivateListingAndAuthenticatesBeforeReading() async throws {
            let folder = try TemporaryFolder()
            let appPath = folder.path + "/Sample.app"
            try FileManager.default.createDirectory(atPath: appPath + "/Contents", withIntermediateDirectories: true)
            let info: [String: Any] = [
                "CFBundleIdentifier": "dev.simmirror.Tests", "CFBundleName": "Sample", "CFBundlePackageType": "APPL",
            ]
            try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0).write(
                to: URL(fileURLWithPath: appPath + "/Contents/Info.plist"))
            let bundle = try #require(Bundle(path: appPath))
            let runtime = MacRuntime(bundle: bundle, home: folder.path, environment: [:], osMajor: 26)
            runtime.start(.init(swiftUIDebugData: true))
            #expect(runtime.isRunning)
            runtime.start(.init())
            defer { runtime.stop() }
            let path = folder.path + "/Library/Application Support/SimMirror/apps/dev.simmirror.Tests.json"
            let listing = try JSONDecoder().decode(Listing.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
            #expect(listing.pid == getpid() && listing.platform == "macos")
            #expect(folder.mode(of: "Library/Application Support/SimMirror/apps/dev.simmirror.Tests.json") == 0o600)
            func ask(_ auth: String, _ origin: String = "", host: String? = nil) async -> RawClient.Answer? {
                await RawClient.send(
                    Data(
                        "GET /v1/hierarchy?window_id=-999 HTTP/1.1\r\nHost: \(host ?? "127.0.0.1:\(listing.port)")\r\nAuthorization: Bearer \(auth)\r\n\(origin)\r\n"
                            .utf8), port: listing.port)
            }
            #expect(await ask("wrong")?.status == 401)
            #expect(await ask(listing.secret, "Origin: https://example.invalid\r\n")?.status == 400)
            #expect(await ask(listing.secret, host: "localhost")?.status == 400)
            #expect(await ask(listing.secret)?.status == 400)  // Tests never show a window.
            runtime.stop()
            #expect(!runtime.isRunning && !FileManager.default.fileExists(atPath: path))
            runtime.stop()
            let preview = MacRuntime(
                bundle: bundle, home: folder.path, environment: ["XCODE_RUNNING_FOR_PREVIEWS": "1"])
            preview.start(.init()); #expect(!preview.isRunning)
            let blocked = MacRuntime(bundle: bundle, home: "/dev/null", environment: [:])
            blocked.start(.init()); #expect(!blocked.isRunning)
            let impatient = MacRuntime(bundle: bundle, home: folder.path, environment: [:], responseWaitS: 0)
            impatient.start(.init()); defer { impatient.stop() }
            let brief = try JSONDecoder().decode(Listing.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
            #expect(await RawClient.request(port: brief.port, secret: brief.secret)?.status == 503)
            SimMirror.start(); _ = SimMirror.isRunning; SimMirror.stop()
        }

        @Test func inputHTTPRequiresAnUnambiguousBoundedBody() {
            func parse(_ headers: String, _ body: String = "{}") -> HTTPParse {
                HTTPRequestParser.parse(Data("POST /v1/input HTTP/1.1\r\n\(headers)\r\n\r\n\(body)".utf8))
            }
            #expect(parse("Content-Length: 2", "{") == .incomplete)
            let good = parse("Content-Length: 2")
            if case .request(let request) = good {
                #expect(request.body == Data("{}".utf8))
            } else {
                Issue.record("valid input did not parse")
            }
            for headers in [
                "", "Content-Length: 0", "Content-Length: 32769", "Content-Length: 2\r\nTransfer-Encoding: chunked",
                "Content-Length: 2\r\ncontent-length: 2",
            ] {
                if case .failure = parse(headers) {} else { Issue.record("ambiguous body was accepted") }
            }
            if case .failure = parse("Content-Length: 1") {} else { Issue.record("trailing bytes were accepted") }
        }
    }
#endif
