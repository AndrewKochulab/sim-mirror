// SPDX-License-Identifier: Apache-2.0
import Foundation

/// A helper belongs to one application process for its whole lifetime. No display or system-wide target exists.
public struct MacWindowOptions: Equatable, Sendable {
    public let pid: Int32
    public let socket: String
    public let windowID: UInt32?
    public let parentPid: Int32?
    public var accessibility = true
    /// Whether the agent is drawn over the window, in a click-through window of the helper's own (`OverlayModel`).
    public var overlay = false

    static func take(_ flags: inout [String: String]) throws -> Self {
        guard let raw = flags.removeValue(forKey: "pid") else { throw HelperFailure("window needs --pid", status: 400) }
        let pid = try Command.pid(raw)
        guard let socket = flags.removeValue(forKey: "socket"), socket.hasPrefix("/") else {
            throw HelperFailure("window needs --socket and an absolute path", status: 400)
        }
        let window = try flags.removeValue(forKey: "window-id").map { raw -> UInt32 in
            guard let id = UInt32(raw), id > 0 else { throw HelperFailure("not a window id: \(raw)", status: 400) }
            return id
        }
        let ax = flags.removeValue(forKey: "accessibility") ?? "on"
        guard ["on", "off"].contains(ax) else { throw HelperFailure("accessibility is on or off", status: 400) }
        let overlay = try flags.removeValue(forKey: "overlay").map(Command.onOff) ?? false
        return Self(pid: pid, socket: socket, windowID: window, parentPid: try flags.removeValue(forKey: "parent-pid").map(Command.pid), accessibility: ax == "on",
                    overlay: overlay)
    }
}

/// Semantic input for a single app. Coordinates are relative to its selected window, never the desktop.
public struct MacAction: Equatable, Sendable, Codable {
    public enum Kind: String, Codable, Sendable {
        case tap, rightClick = "right_click", hover, scroll, type, keys, menu, info, window
    }
    public let kind: Kind
    public var x: Double?
    public var y: Double?
    public var dx: Double?
    public var dy: Double?
    public var text: String?
    public var path: [String]?
    public var windowID: UInt32?
    public var clear: Bool?

    public init(kind: Kind, x: Double? = nil, y: Double? = nil, dx: Double? = nil, dy: Double? = nil,
                text: String? = nil, path: [String]? = nil, windowID: UInt32? = nil, clear: Bool? = nil) {
        self.kind = kind; self.x = x; self.y = y; self.dx = dx; self.dy = dy
        self.text = text; self.path = path; self.windowID = windowID; self.clear = clear
    }

    public func validate() throws {
        switch kind {
        case .tap, .rightClick, .hover, .scroll:
            guard let x, let y, x.isFinite, y.isFinite, x >= 0, y >= 0 else {
                throw HelperFailure("input needs finite window coordinates", status: 400)
            }
            if kind == .scroll {
                guard let dx, let dy, dx.isFinite, dy.isFinite, abs(dx) <= 10000, abs(dy) <= 10000 else {
                    throw HelperFailure("scroll needs finite dx and dy within 10000 points", status: 400)
                }
            }
        case .type, .keys:
            guard let text, !text.isEmpty, text.count <= 2000, !text.contains("\0") else {
                throw HelperFailure("input needs 1 to 2000 characters without NUL", status: 400)
            }
        case .menu:
            guard let path, (1...8).contains(path.count), path.allSatisfy({ !$0.isEmpty && $0.count <= 200 }) else {
                throw HelperFailure("menu needs 1 to 8 nonempty titles of at most 200 characters", status: 400)
            }
        case .window:
            guard let windowID, windowID > 0 else { throw HelperFailure("choose a window id of this app", status: 400) }
        case .info: break
        }
    }
}

/// A menu item's title as a person reads it. Apps that show a shortcut hint in the title -- after a tab, or after a run
/// of spaces ("Library     g l") -- are chosen by the words before it, as well as by the whole title.
public enum MenuTitle {
    public static func matches(_ title: String, _ wanted: String) -> Bool {
        title == wanted || shown(title) == wanted
    }

    static func shown(_ title: String) -> String {
        var name = title.firstIndex(of: "\t").map { String(title[..<$0]) } ?? title
        if let run = name.range(of: "  ") { name = String(name[..<run.lowerBound]) }
        return name.trimmingCharacters(in: .whitespaces)
    }
}

/// Chromium can append a window-sized structural sibling after its web content. Pick the deepest hit, not the last
/// overlapping sibling. Framework adapters supply only owned children; the walk is bounded even for malformed trees.
public enum MacHitSearch {
    public static func deepest<Node: Hashable>(_ root: Node, x: Double, y: Double,
                                               bounds: (Node) -> Rect?, children: (Node) -> [Node]) -> Node? {
        var seen = Set<Node>()
        func visit(_ node: Node, depth: Int) -> (Node, Int)? {
            guard depth < 40, seen.count < 3000, seen.insert(node).inserted else { return nil }
            let rect = bounds(node)
            let contains = rect.map { $0.width > 0 && $0.height > 0 && x >= $0.x && y >= $0.y
                && x < $0.x + $0.width && y < $0.y + $0.height } ?? false
            if let rect, rect.width > 0, rect.height > 0, !contains { return nil }
            var found: (Node, Int)? = contains ? (node, depth) : nil
            for child in children(node) {
                if let match = visit(child, depth: depth + 1), match.1 >= (found?.1 ?? -1) { found = match }
            }
            return found
        }
        return visit(root, depth: 0)?.0
    }
}

/// One window an app process owns, as the window server lists it: what choosing the window to show needs.
public struct MacWindowCandidate: Equatable, Sendable {
    public let id: UInt32
    public let pid: Int32
    /// The window level; 0 is the normal level every document and app window sits at.
    public let layer: Int
    public let width: Double
    public let height: Double
    public let onScreen: Bool

    public init(id: UInt32, pid: Int32, layer: Int, width: Double, height: Double, onScreen: Bool) {
        self.id = id; self.pid = pid; self.layer = layer; self.width = width; self.height = height; self.onScreen = onScreen
    }
}

/// Which of an app's windows a helper shows. Left to choose, it takes only a real app window: one on screen, at the
/// normal window level, at least `minimumSide` points each way. An app that has not shown its main window yet -- one
/// waiting on a Keychain prompt -- owns only status-item and menu-bar strips (as wide as the display, about 33 points
/// tall, off screen, some at level 0), panels, menus and tooltips above level 0, and hidden or minimised windows: none
/// of them is a window a person works in, so with none of the first kind the helper refuses, and is asked again later,
/// rather than binding a strip that ends under it. A window chosen by its id need only belong to the process.
public enum MacWindowChoice {
    /// Smaller than any app's main window, larger than any menu-bar or status-item strip (at most 40 points tall).
    public static let minimumSide = 64.0

    public static func eligible(_ window: MacWindowCandidate, pid: Int32) -> Bool {
        window.pid == pid && window.layer == 0 && window.onScreen
            && window.width >= minimumSide && window.height >= minimumSide
    }

    /// The window to show: the one asked for when it is this process's, else the largest eligible one; nil for none.
    public static func choose(_ windows: [MacWindowCandidate], pid: Int32, wanted: UInt32?) -> MacWindowCandidate? {
        if let wanted {
            return windows.first { $0.id == wanted && $0.pid == pid && $0.width > 1 && $0.height > 1 }
        }
        return windows.filter { eligible($0, pid: pid) }.max { $0.width * $0.height < $1.width * $1.height }
    }
}
