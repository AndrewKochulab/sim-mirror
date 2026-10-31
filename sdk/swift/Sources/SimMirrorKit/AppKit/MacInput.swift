// SPDX-License-Identifier: Apache-2.0
#if DEBUG && os(macOS)
    import AppKit
    import Foundation

    struct MacCommand: Codable, Sendable {
        var kind: String
        var windowID: Int
        var x: Double?
        var y: Double?
        var dx: Double?
        var dy: Double?
        var text: String?
        var path: [String]?
        var clear: Bool?
        var submit: Bool?
    }
    struct MacInputFailure: Error { let message: String }

    /// All input is delivered inside the app. No event is posted to the WindowServer and the app is never activated.
    @MainActor
    enum MacInput {
        static func perform(
            _ command: MacCommand, windows: [NSWindow],
            enqueue: (NSEvent) -> Void = { NSApp.postEvent($0, atStart: false) }
        ) throws {
            guard let window = windows.first(where: { $0.windowNumber == command.windowID }), window.isVisible,
                let root = window.contentView
            else { throw MacInputFailure(message: "choose a visible window of this app") }
            switch command.kind {
            case "tap", "right_click", "hover", "scroll":
                guard let x = command.x, let y = command.y, x.isFinite, y.isFinite,
                    x >= 0, y >= 0, x < window.frame.width, y < window.frame.height
                else {
                    throw MacInputFailure(message: "input is outside this app window")
                }
                let point = NSPoint(x: x, y: window.frame.height - y)
                // hitTest takes its superview's coordinates, including when the content view is flipped.
                let hitPoint = root.superview?.convert(point, from: nil) ?? point
                guard let view = root.hitTest(hitPoint) else {
                    throw MacInputFailure(message: "there is no app view at that point")
                }
                if command.kind == "tap" {
                    let screenPoint = window.convertPoint(toScreen: point)
                    let hit = root.accessibilityHitTest(screenPoint)
                    if MacAccess(hit)?.press() == true { return }
                    var ancestor: NSView? = view
                    while let current = ancestor {
                        if let field = current as? NSTextField { window.makeFirstResponder(field); return }
                        if let control = current as? NSButton { control.performClick(nil); return }
                        ancestor = current.superview
                    }
                    let down = try mouse(.leftMouseDown, window, point)
                    let up = try mouse(.leftMouseUp, window, point)
                    // Custom views receive their own responder method while inactive; NSWindow drops that first click.
                    // Queue the up first because a view may track synchronously inside mouseDown.
                    enqueue(up)
                    view.mouseDown(with: down)
                    view.mouseUp(with: up)
                } else if command.kind == "right_click" {
                    throw MacInputFailure(message: "menu tracking can take focus; use a menu title path")
                } else if command.kind == "hover" {
                    view.mouseMoved(with: try mouse(.mouseMoved, window, point))
                } else {
                    guard let dx = command.dx, let dy = command.dy, dx.isFinite, dy.isFinite,
                        abs(dx) <= 10000, abs(dy) <= 10000
                    else { throw MacInputFailure(message: "scroll needs bounded dx and dy") }
                    if let scroll = view.enclosingScrollView ?? view as? NSScrollView,
                        let document = scroll.documentView
                    {
                        var origin = scroll.contentView.bounds.origin
                        origin.x += dx
                        origin.y += document.isFlipped ? dy : -dy
                        scroll.contentView.scroll(to: origin)
                        scroll.reflectScrolledClipView(scroll.contentView)
                    } else {
                        throw MacInputFailure(message: "this view has no in-process scroll container")
                    }
                }
            case "type":
                guard let text = command.text, !text.isEmpty, text.count <= 2000, !text.contains("\0"),
                    let target = window.firstResponder as? any NSTextInputClient
                else {
                    throw MacInputFailure(message: "choose a text field before typing")
                }
                if command.clear == true {
                    guard window.firstResponder?.tryToPerform(#selector(NSText.selectAll(_:)), with: nil) == true else {
                        throw MacInputFailure(message: "this field cannot select its contents for replacement")
                    }
                }
                target.insertText(text, replacementRange: NSRange(location: NSNotFound, length: 0))
                if command.submit == true {
                    try perform(
                        MacCommand(kind: "keys", windowID: window.windowNumber, text: "return"), windows: windows)
                }
            case "keys":
                guard let text = command.text else { throw MacInputFailure(message: "keys needs a chord") }
                var parts = text.lowercased().split(separator: "+").map(String.init)
                guard let key = parts.popLast(), parts.allSatisfy({ ["cmd", "shift", "alt", "ctrl"].contains($0) })
                else {
                    throw MacInputFailure(message: "unknown key chord")
                }
                let special: [String: (String, UInt16)] = [
                    "return": ("\r", 36), "escape": ("\u{1b}", 53), "tab": ("\t", 48), "delete": ("\u{7f}", 51),
                    "space": (" ", 49), "left": ("\u{f702}", 123), "right": ("\u{f703}", 124),
                    "down": ("\u{f701}", 125), "up": ("\u{f700}", 126),
                ]
                let pair = special[key] ?? (key, 0)
                guard pair.0.count == 1, !(parts.contains("cmd") && key == "tab") else {
                    throw MacInputFailure(message: "this chord is not app-scoped")
                }
                var flags: NSEvent.ModifierFlags = []
                if parts.contains("cmd") { flags.insert(.command) }
                if parts.contains("shift") { flags.insert(.shift) }
                if parts.contains("alt") { flags.insert(.option) }
                if parts.contains("ctrl") { flags.insert(.control) }
                guard
                    let event = NSEvent.keyEvent(
                        with: .keyDown, location: .zero, modifierFlags: flags,
                        timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                        context: nil, characters: pair.0, charactersIgnoringModifiers: pair.0,
                        isARepeat: false, keyCode: pair.1)
                else { throw MacInputFailure(message: "could not create the key event") }
                if !window.performKeyEquivalent(with: event) { window.firstResponder?.keyDown(with: event) }
            case "menu":
                guard let path = command.path, (1...8).contains(path.count), let rootMenu = NSApp.mainMenu,
                    !["Apple", "Services"].contains(path[0])
                else { throw MacInputFailure(message: "choose an app menu path") }
                var menu = rootMenu
                for (index, title) in path.enumerated() {
                    guard let item = menu.items.first(where: { $0.title == title }), item.isEnabled else {
                        throw MacInputFailure(message: "that menu item is absent or disabled")
                    }
                    if index == path.count - 1 {
                        guard item.submenu == nil, item.action != nil else {
                            throw MacInputFailure(message: "choose a leaf menu action")
                        }
                        menu.performActionForItem(at: menu.index(of: item)); return
                    }
                    guard let child = item.submenu else { throw MacInputFailure(message: "that item has no submenu") }
                    menu = child
                }
            default: throw MacInputFailure(message: "unsupported app-scoped input")
            }
        }
        private static func mouse(_ type: NSEvent.EventType, _ window: NSWindow, _ point: NSPoint) throws -> NSEvent {
            guard
                let event = NSEvent.mouseEvent(
                    with: type, location: point, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                    windowNumber: window.windowNumber, context: nil, eventNumber: 0, clickCount: 1, pressure: 1)
            else { throw MacInputFailure(message: "could not create the mouse event") }
            return event
        }
    }
#endif
