// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

@Suite struct AccessibilityTests {
    @Test func traitsAreNamedBitByBit() {
        #expect(AXTraits.names(0) == ["None"])
        #expect(AXTraits.names(1 | 1 << 16 | 1 << 33) == ["Button", "Header", "Scrollable"])
        #expect(AXTraits.names(1 << 53) == ["Toggle"])
        #expect(AXTraits.names(1 << 58 | 1 << 60) == ["AllowsLayoutChangeInStatusBar", "Unknown"])
        #expect(AXTraits.names.count == 59)
    }

    @Test func aRoleIsItsNameWithoutItsPrefix() {
        #expect(AXDocument.type(of: "AXButton") == "Button")
        #expect(AXDocument.type(of: "Custom") == "Custom")
        #expect(AXDocument.type(of: nil) == nil)
    }

    @Test func anElementIsKeptForItsLabelIdentifierOrRoleAndOthersGiveWayToTheirChildren() {
        let button = AXNode(role: "AXButton", frame: Rect(x: 1, y: 2, width: 3, height: 4))
        let group = AXNode(role: "AXGroup", label: "", identifier: "", children: [button, AXNode(role: "AXGroup")])
        let app = AXNode(role: "AXApplication", label: "Settings", children: [group, AXNode(role: "AXStaticText", identifier: "id")])
        let document = AXDocument.make(root: app, screen: iPhone17)
        guard case .object(let fields) = document, case .array(let elements) = fields["elements"] else {
            Issue.record("not a document")
            return
        }
        #expect(fields["backend"] == .string("native") && fields["modal"] == .null && fields["truncated"] == .bool(false))
        #expect(fields["screen"] == .object(["coordinate_space": .string("screen"), "width": .int(402), "height": .int(874)]))
        guard elements.count == 1, case .object(let root) = elements[0], case .array(let children) = root["children"] else {
            Issue.record("no application")
            return
        }
        #expect(root["type"] == .string("Application") && root["label"] == .string("Settings"))
        #expect(children.count == 2)
        #expect(AXDocument.kept(AXNode(role: "AXGroup")) == [])
    }

    @Test func anElementSaysEverythingIdbSaysOfIt() {
        let node = AXNode(
            role: "AXCheckBox", subrole: "AXSwitch", roleDescription: "switch", label: "Wi-Fi", title: "t", identifier: "wifi",
            help: "h", value: .text("1"), frame: Rect(x: 16, y: 229.5, width: 370, height: 28), traits: 1 | 1 << 53, enabled: false,
            required: true, pid: 42, customActions: ["More"]
        )
        let expected = JSONValue.object([
            "type": .string("CheckBox"), "subrole": .string("AXSwitch"), "role_description": .string("switch"),
            "label": .string("Wi-Fi"), "title": .string("t"), "identifier": .string("wifi"), "help": .string("h"),
            "value": .string("1"),
            "frame": .object(["x": .number(16), "y": .number(229.5), "width": .number(370), "height": .number(28)]),
            "traits": .array([.string("Button"), .string("Toggle")]), "enabled": .bool(false), "content_required": .bool(true),
            "pid": .int(42), "custom_actions": .array([.string("More")]), "children": .array([]),
        ])
        #expect(AXDocument.element(node, children: []) == expected)
        let bare = AXDocument.element(AXNode(value: .number(0.25)), children: [])
        guard case .object(let fields) = bare else { return }
        #expect(fields["value"] == .number(0.25) && fields["traits"] == .null && fields["type"] == .null)
        #expect(AXDocument.element(AXNode(), children: []) != expected)
    }
}
