// SPDX-License-Identifier: Apache-2.0
import Foundation

/// A rectangle in points, from the top left.
public struct Rect: Equatable, Sendable {
    public let x: Double
    public let y: Double
    public let width: Double
    public let height: Double

    public init(x: Double, y: Double, width: Double, height: Double) {
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }

    var json: JSONValue {
        .object(["x": .number(x), "y": .number(y), "width": .number(width), "height": .number(height)])
    }
}

/// An accessibility value: text, a number, or a switch's state.
public enum AccessibilityValue: Equatable, Sendable {
    case text(String)
    case number(Double)

    var json: JSONValue {
        switch self {
        case .text(let text): return .string(text)
        case .number(let number): return .number(number)
        }
    }
}

/// One element of a screen's accessibility tree, read from the device.
///
/// Only what SimMirror reads of an element is kept. Each attribute is a round trip to the simulator, and a snapshot
/// reads every element, so an attribute nothing reads -- a role's description, help text, custom actions -- is not
/// asked for at all.
public struct AXNode: Equatable, Sendable {
    /// Its role as the accessibility API says it, ``AXButton``.
    public var role: String?
    public var subrole: String?
    public var label: String?
    public var title: String?
    public var identifier: String?
    public var value: AccessibilityValue?
    public var frame: Rect
    /// Its traits as the bits iOS keeps them in; nil when it does not say.
    public var traits: UInt64?
    public var enabled: Bool
    public var children: [AXNode]

    public init(
        role: String? = nil, subrole: String? = nil, label: String? = nil, title: String? = nil,
        identifier: String? = nil, value: AccessibilityValue? = nil, frame: Rect = Rect(x: 0, y: 0, width: 0, height: 0),
        traits: UInt64? = nil, enabled: Bool = true, children: [AXNode] = []
    ) {
        self.role = role
        self.subrole = subrole
        self.label = label
        self.title = title
        self.identifier = identifier
        self.value = value
        self.frame = frame
        self.traits = traits
        self.enabled = enabled
        self.children = children
    }
}

/// The names of the traits iOS gives an element, bit by bit.
///
/// The bits are AXRuntime's ``kAX*Trait`` constants, which run from 0 upward in this order; the names are the constants
/// less their prefix and suffix, which is how idb_companion names them too.
public enum AXTraits {
    public static let names = [
        "Button", "Link", "Image", "Selected", "PlaysSound", "KeyboardKey", "StaticText", "SummaryElement",
        "NotEnabled", "UpdatesFrequently", "SearchField", "StartsMediaSession", "Adjustable",
        "AllowsDirectInteraction", "CausesPageTurn", "TabBar", "Header", "WebContent", "TextEntry", "PickerElement",
        "RadioButton", "IsEditing", "LaunchIcon", "StatusBarElement", "SecureTextField", "Inactive", "Footer",
        "BackButton", "TabButton", "AutoCorrectCandidate", "DeleteKey", "SelectionDismissesItem", "Visited",
        "Scrollable", "Spacer", "TableIndex", "Map", "TextOperationsAvailable", "Draggable", "GesturePracticeRegion",
        "PopupButton", "AllowsNativeSliding", "MathEquation", "ContainedByTable", "ContainedByList", "TouchContainer",
        "SupportsZoom", "TextArea", "BookContent", "ContainedByLandmark", "FolderIcon", "ReadOnly", "MenuItem",
        "Toggle", "IgnoreItemChooser", "SupportsTrackingDetail", "Alert", "ContainedByFieldset",
        "AllowsLayoutChangeInStatusBar",
    ]

    /// The names of the bits set: ``None`` for none, and ``Unknown`` for bits no name is known for.
    public static func names(_ mask: UInt64) -> [String] {
        guard mask != 0 else { return ["None"] }
        var found = names.indices.filter { mask & (1 << UInt64($0)) != 0 }.map { names[$0] }
        if mask >> UInt64(names.count) != 0 { found.append("Unknown") }
        return found
    }
}

/// The document SimMirror reads a screen from: the tree of the elements a person or an agent can act on or read.
///
/// Its shape is idb_companion's consolidated accessibility document, with the keys SimMirror reads, so SimMirror reads
/// either one the same way. The
/// tree keeps what is worth reporting -- an element with a label, an identifier, or a role that is acted on -- and an
/// element that is not has its kept descendants take its place, so a button inside an unlabelled group is kept.
public enum AXDocument {
    /// The roles, less their ``AX`` prefix, that are kept whatever they say.
    public static let actionableRoles: Set<String> = [
        "Button", "Cell", "TextField", "SecureTextField", "SearchField", "Switch", "Toggle", "Link", "MenuItem",
        "Slider", "CheckBox", "RadioButton", "SegmentedControl", "Stepper", "PopUpButton", "Picker", "PickerWheel",
        "Tab", "Key", "DisclosureTriangle",
    ]

    public static func make(root: AXNode, screen: ScreenGeometry) -> JSONValue {
        let elements = kept(root)
        return .object([
            "backend": .string("native"),
            "elements": .array(elements),
            "modal": .null,
            "screen": .object([
                "coordinate_space": .string("screen"),
                "width": .int(screen.widthPt),
                "height": .int(screen.heightPt),
            ]),
            "truncated": .bool(false),
        ])
    }

    /// A role without its ``AX`` prefix: ``AXButton`` is ``Button``.
    public static func type(of role: String?) -> String? {
        guard let role else { return nil }
        return role.hasPrefix("AX") ? String(role.dropFirst(2)) : role
    }

    static func keeps(_ node: AXNode) -> Bool {
        if let label = node.label, !label.isEmpty { return true }
        if let identifier = node.identifier, !identifier.isEmpty { return true }
        return type(of: node.role).map(actionableRoles.contains) ?? false
    }

    static func kept(_ node: AXNode) -> [JSONValue] {
        let children = node.children.flatMap(kept)
        guard keeps(node) else { return children }
        return [element(node, children: children)]
    }

    static func element(_ node: AXNode, children: [JSONValue]) -> JSONValue {
        .object([
            "type": .text(type(of: node.role)),
            "subrole": .text(node.subrole),
            "label": .text(node.label),
            "title": .text(node.title),
            "identifier": .text(node.identifier),
            "value": node.value?.json ?? .null,
            "frame": node.frame.json,
            "traits": node.traits.map { .array(AXTraits.names($0).map(JSONValue.string)) } ?? .null,
            "enabled": .bool(node.enabled),
            "children": .array(children),
        ])
    }
}
