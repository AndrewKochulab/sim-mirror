// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import SimMirrorKit

struct DebugDataTests {
    @Test func debugDataIsReadWhenAskedAndOnIOS26Only() {
        let os26 = OperatingSystemVersion(majorVersion: 26, minorVersion: 5, patchVersion: 0)
        let os27 = OperatingSystemVersion(majorVersion: 27, minorVersion: 0, patchVersion: 0)
        #expect(DebugDataPolicy.decide(requested: false, os: os26) == .off)
        #expect(DebugDataPolicy.decide(requested: true, os: os26) == .on)
        #expect(
            DebugDataPolicy.decide(requested: true, os: os27)
                == .refused("SwiftUI's debug data is read on iOS 26 only, not iOS 27.0"))
    }

    /// A debug data node in the shape SwiftUI serializes it: numbered properties, each with an attribute.
    static func raw(
        _ type: String, at position: [Double]? = nil, size: [Double]? = nil, value: [[String: Any]] = [],
        children: [[String: Any]] = []
    ) -> [String: Any] {
        var properties: [[String: Any]] = [["id": 0, "attribute": ["readableType": type, "flags": 1]]]
        if let position { properties.append(["id": 3, "attribute": ["value": position, "readableType": "CGPoint"]]) }
        if let size { properties.append(["id": 4, "attribute": ["value": size, "readableType": "CGSize"]]) }
        properties.append(["id": 1, "attribute": ["readableType": type, "subattributes": value]])
        return ["properties": properties, "children": children]
    }

    static func text(_ key: String, formatted: Bool = false) -> [[String: Any]] {
        [
            [
                "name": "storage",
                "subattributes": [
                    [
                        "name": "key",
                        "subattributes": [
                            ["name": "key", "value": key, "readableType": "String"],
                            ["name": "hasFormatting", "value": formatted, "readableType": "Bool"],
                        ],
                    ]
                ],
            ]
        ]
    }

    static func image(_ name: String) -> [[String: Any]] {
        [
            [
                "name": "provider",
                "subattributes": [
                    ["name": "name", "value": name], ["name": "location", "value": "system"],
                    ["name": "label", "subattributes": [["name": "systemSymbol", "value": name]]],
                ],
            ]
        ]
    }

    static func data(_ roots: [[String: Any]]) throws -> Data {
        try JSONSerialization.data(withJSONObject: roots)
    }

    @Test func theDecoderReadsTypesPlacesTextAndImageNames() throws {
        let card = Self.raw(
            "TapGestureModifier", at: [16, 89], size: [370, 93],
            children: [
                Self.raw("Image", at: [180, 105], size: [42, 41], value: Self.image("sun.max")),
                Self.raw("Text", at: [159, 146], size: [83, 20], value: Self.text("Daily mix %lld", formatted: true)),
                Self.raw("Text", value: Self.text("Welcome")),
            ])
        let decoded = DebugDataDecoder.decode(try Self.data([card]))
        guard case .nodes(let roots) = decoded, roots.count == 1, roots[0].children.count == 3 else {
            Issue.record("not decoded: \(decoded)")
            return
        }
        #expect(roots[0].type == "TapGestureModifier" && roots[0].frame == CGRect(x: 16, y: 89, width: 370, height: 93))
        #expect(roots[0].children.map(\.imageName) == ["sun.max", nil, nil])
        #expect(roots[0].children.map(\.text) == [nil, nil, "Welcome"])
        #expect(roots[0].children[2].frame == nil)
    }

    @Test func dataThatIsEmptyOrOfAnotherShapeSaysSo() throws {
        #expect(DebugDataDecoder.decode(nil) == .unsupported)
        #expect(DebugDataDecoder.decode(Data("{}".utf8)) == .unsupported)
        #expect(DebugDataDecoder.decode(Data("[]".utf8)) == .empty)
        #expect(DebugDataDecoder.decode(Data("[1]".utf8)) == .unsupported)
        #expect(DebugDataDecoder.decode(try Self.data([["properties": [["id": 1]]]])) == .unsupported)
        let badChild = Self.raw("VStack", children: [["children": []]])
        #expect(DebugDataDecoder.decode(try Self.data([badChild])) == .unsupported)
        let oddPlace = Self.raw("Text", at: [1], size: [1, 2, 3])
        #expect(DebugDataDecoder.decode(try Self.data([oddPlace])) == .nodes([DebugNode(type: "Text")]))
        #expect(
            DebugDataDecoder.plainText(in: [["name": "hasFormatting", "value": false], ["name": "key", "value": ""]])
                == nil)
        #expect(DebugDataDecoder.imageName(in: [["name": "name", "value": "star"]]) == nil)
    }

    private func node(
        _ type: String, _ frame: CGRect?, text: String? = nil, image: String? = nil, _ children: [DebugNode] = []
    )
        -> DebugNode
    {
        DebugNode(
            type: type, position: frame?.origin, size: frame?.size, text: text, imageName: image, children: children)
    }

    /// The probe screen as SwiftUI laid it out: a scroll view's content starts again at zero, and a switch and a
    /// UIKit view of known screen frames pin it down.
    private var screen: [DebugNode] {
        [
            node(
                "SafeArea", CGRect(x: 0, y: 0, width: 402, height: 874),
                [
                    node(
                        "AnyView", CGRect(x: 0, y: 168, width: 402, height: 672),
                        [
                            node(
                                "ScrollContent", CGRect(x: 0, y: 0, width: 402, height: 477),
                                [
                                    node("Switch", CGRect(x: 170.5, y: 198, width: 61, height: 28)),
                                    node("UIKitBox", CGRect(x: 16, y: 365, width: 370, height: 60)),
                                    node(
                                        "TapGestureModifier", CGRect(x: 16, y: 89, width: 370, height: 93),
                                        [
                                            node(
                                                "Image", CGRect(x: 180, y: 105, width: 42, height: 41), image: "sun.max"
                                            ),
                                            node(
                                                "Text", CGRect(x: 159, y: 146, width: 83, height: 20), text: "Daily mix"
                                            ),
                                        ]),
                                    node(
                                        "TapGestureModifier", CGRect(x: 16, y: 20, width: 100, height: 40),
                                        [node("Image", CGRect(x: 20, y: 20, width: 20, height: 20), image: "star")]),
                                    node("TapGestureModifier", CGRect(x: 16, y: 20, width: 0, height: 40)),
                                ])
                        ])
                ])
        ]
    }

    @Test func findingsArePlacedByThePlatformViewsOfTheirSpace() {
        let scrolled: CGFloat = 116
        let platformViews = [
            CGRect(x: 170.5, y: 198 + scrolled, width: 61, height: 28),
            CGRect(x: 16, y: 365 + scrolled, width: 370, height: 60),
        ]
        let found = DebugDataPlacer.findings(
            in: screen, platformViews: platformViews, within: CGRect(x: 0, y: 0, width: 402, height: 874))
        let tap = { (label: String, source: LabelSource, frame: CGRect) in
            DebugFinding(kind: .tap, label: label, labelSource: source, frame: frame)
        }
        let image = { (label: String, frame: CGRect) in
            DebugFinding(kind: .image, label: label, labelSource: .image, frame: frame)
        }
        #expect(
            found == [
                tap("Daily mix", .descendants, CGRect(x: 16, y: 205, width: 370, height: 93)),
                image("sun.max", CGRect(x: 180, y: 221, width: 42, height: 41)),
                tap("star", .image, CGRect(x: 16, y: 136, width: 100, height: 40)),
                image("star", CGRect(x: 20, y: 136, width: 20, height: 20)),
            ])
    }

    @Test func aSpaceWithNoPlatformViewOrOnesThatDisagreeIsLeftOut() {
        let bounds = CGRect(x: 0, y: 0, width: 402, height: 874)
        #expect(DebugDataPlacer.findings(in: screen, platformViews: [], within: bounds).isEmpty)
        let disagreeing = [
            CGRect(x: 170.5, y: 300, width: 61, height: 28), CGRect(x: 16, y: 900, width: 370, height: 60),
        ]
        #expect(DebugDataPlacer.findings(in: screen, platformViews: disagreeing, within: bounds).isEmpty)
        let twins = [CGRect(x: 0, y: 0, width: 61, height: 28), CGRect(x: 100, y: 0, width: 61, height: 28)]
        #expect(DebugDataPlacer.findings(in: screen, platformViews: twins, within: bounds).isEmpty)
        let outside = [CGRect(x: 170.5, y: 198 + 2000, width: 61, height: 28)]
        #expect(DebugDataPlacer.findings(in: screen, platformViews: outside, within: bounds).isEmpty)
    }

    @Test func aTappedViewIsNamedByItsTextBeforeItsImage() {
        let said = { (node: DebugNode) in DebugDataPlacer.name(inside: node).map { "\($0.0) | \($0.1.rawValue)" } }
        #expect(
            said(node("Tap", nil, [node("Image", nil, image: "i"), node("Text", nil, text: "t")])) == "t | descendants")
        #expect(said(node("Tap", nil, [node("Image", nil, image: "i")])) == "i | image")
        #expect(said(node("Tap", nil)) == nil)
    }

    @Test func findingsNameAndMakeTappableTheNodesInTheirPlaceOrAreAdded() throws {
        let card = testNode(.container, frame: CGRect(x: 16, y: 205, width: 370, height: 93))
        let icon = testNode(.image, frame: CGRect(x: 180, y: 221, width: 42, height: 41))
        let named = testNode(.image, label: "Kept", frame: CGRect(x: 0, y: 0, width: 20, height: 20))
        let findings = [
            DebugFinding(
                kind: .tap, label: "Daily mix", labelSource: .descendants,
                frame: CGRect(x: 16, y: 205, width: 370, height: 93)),
            DebugFinding(
                kind: .image, label: "sun.max", labelSource: .image,
                frame: CGRect(x: 180, y: 221, width: 42, height: 41)),
            DebugFinding(
                kind: .image, label: "star", labelSource: .image, frame: CGRect(x: 0, y: 0, width: 20, height: 20)),
            DebugFinding(
                kind: .tap, label: "Open", labelSource: .descendants,
                frame: CGRect(x: 0, y: 400, width: 100, height: 40)),
            DebugFinding(
                kind: .tap, label: "Open again", labelSource: .descendants,
                frame: CGRect(x: 0, y: 400, width: 100, height: 40)),
            DebugFinding(
                kind: .image, label: "nowhere", labelSource: .image,
                frame: CGRect(x: 300, y: 700, width: 10, height: 10)),
        ]
        let merged = DebugFindingMerger.merge(findings, into: [card, icon, named])
        try #require(merged.count == 4)
        #expect(merged[0].interactive && merged[0].label == "Daily mix" && merged[0].labelSource == .descendants)
        #expect(merged[1].label == "sun.max" && !merged[1].interactive)
        #expect(merged[2].label == "Kept")
        #expect(merged[3].kind == .button && merged[3].label == "Open" && merged[3].source == .swiftui)
        #expect(merged[3].typeName == DebugFindingMerger.tapTypeName)
    }
}
