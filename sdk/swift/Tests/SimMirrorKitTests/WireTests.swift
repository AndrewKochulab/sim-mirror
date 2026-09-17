// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import SimMirrorKit

/// The Swift model and protocol/app-sdk/v1 are one protocol: every example reads into the model and writes back the
/// same document, and every name the model writes is one the schema has.
struct WireTests {
    static let examples = "protocol/app-sdk/v1/examples"

    @Test(arguments: [
        "hierarchy-uikit", "hierarchy-swiftui", "hierarchy-tagged", "hierarchy-alert", "hierarchy-keyboard",
    ])
    func aHierarchyExampleReadsAndWritesBackTheSameDocument(name: String) throws {
        let data = try Repository.data("\(Self.examples)/\(name).json")
        let hierarchy = try JSONDecoder().decode(Hierarchy.self, from: data)
        #expect(try document(WireJSON.encode(hierarchy)) == document(data))
    }

    @Test func theListingAndErrorExamplesReadAndWriteBackTheSameDocument() throws {
        let listing = try Repository.data("\(Self.examples)/listing.json")
        #expect(try document(WireJSON.encode(JSONDecoder().decode(Listing.self, from: listing))) == document(listing))
        for name in ["error-inactive", "error-unauthorized"] {
            let data = try Repository.data("\(Self.examples)/\(name).json")
            #expect(try document(WireJSON.encode(JSONDecoder().decode(ErrorBody.self, from: data))) == document(data))
        }
    }

    private func schema(_ name: String) throws -> [String: Any] {
        try #require(try Repository.json("protocol/app-sdk/v1/\(name).schema.json") as? [String: Any])
    }

    private func definition(_ schema: [String: Any], _ name: String) throws -> [String: Any] {
        let definitions = try #require(schema["$defs"] as? [String: Any])
        return try #require(definitions[name] as? [String: Any])
    }

    private func keys(of value: some Encodable) throws -> Set<String> {
        let object = try #require(try JSONSerialization.jsonObject(with: WireJSON.encode(value)) as? [String: Any])
        return Set(object.keys)
    }

    private func properties(_ schema: [String: Any]) throws -> Set<String> {
        Set(try #require(schema["properties"] as? [String: Any]).keys)
    }

    private func enumeration(_ schema: [String: Any], _ name: String) throws -> Set<String> {
        Set(try #require(try definition(schema, name)["enum"] as? [String]))
    }

    @Test func everyNameTheModelWritesIsOneTheSchemaHas() throws {
        let hierarchy = try schema("hierarchy")
        let listing = try schema("listing")
        let sample = testHierarchy(nodes: [testNode()])
        #expect(try keys(of: sample) == properties(hierarchy))
        #expect(try keys(of: sample.app) == properties(definition(hierarchy, "App")))
        #expect(try keys(of: sample.screen) == properties(definition(hierarchy, "Screen")))
        #expect(try keys(of: Modal(kind: .sheet)) == properties(definition(hierarchy, "Modal")))
        #expect(try keys(of: Keyboard(frame: Frame(.zero))) == properties(definition(hierarchy, "Keyboard")))
        #expect(try keys(of: sample.windows[0]) == properties(definition(hierarchy, "Window")))
        #expect(try keys(of: testNode()) == properties(definition(hierarchy, "Node")))
        #expect(try keys(of: Frame(.zero)) == properties(definition(hierarchy, "Frame")))
        let written = Listing(
            protocolVersion: 1, sdkVersion: "1", deviceUDID: "", bundleID: "", name: "", pid: 1, port: 1, secret: "",
            active: true, startedAt: "")
        #expect(try keys(of: written) == properties(listing))
        #expect(try keys(of: ErrorBody(.busy, "")) == properties(definition(hierarchy, "ErrorBody")))
    }

    @Test func everyEnumerationIsTheSchemas() throws {
        let hierarchy = try schema("hierarchy")
        #expect(Set(SimMirror.Kind.allCases.map(\.rawValue)) == (try enumeration(hierarchy, "Kind")))
        #expect(Set(ErrorCode.allCases.map(\.rawValue)) == (try enumeration(hierarchy, "ErrorCode")))
        let labelSources: [LabelSource] = [
            .accessibility, .title, .text, .descendants, .image, .identifier, .type, .tag,
        ]
        #expect(Set(labelSources.map(\.rawValue)) == (try enumeration(hierarchy, "LabelSource")))
        #expect(Set([Trait.selected, .editing].map(\.rawValue)) == (try enumeration(hierarchy, "Trait")))
        #expect(Set([NodeSource.uikit, .swiftui, .tag].map(\.rawValue)) == (try enumeration(hierarchy, "NodeSource")))
        #expect(
            Set([ModalKind.alert, .sheet, .fullScreen, .popover].map(\.rawValue))
                == (try enumeration(hierarchy, "ModalKind")))
        let orientations: [Orientation] = [.portrait, .portraitUpsideDown, .landscapeLeft, .landscapeRight, .unknown]
        #expect(Set(orientations.map(\.rawValue)) == (try enumeration(hierarchy, "Orientation")))
        #expect(ErrorCode.allCases.map(\.status) == [400, 401, 404, 405, 409, 431, 503, 507])
    }

    @Test func theProtocolVersionIsTheOneTheExamplesSpeak() throws {
        let example = try #require(try Repository.json("\(Self.examples)/hierarchy-uikit.json") as? [String: Any])
        #expect(example["protocol"] as? Int == WireProtocol.version)
        #expect(WireProtocol.hierarchyPath == "/v1/hierarchy")
    }

    @Test func aNodeSaysNullForWhatItDoesNotHaveAndNoSourceWithoutALabel() throws {
        let node = Node(kind: .text, labelSource: .text, frame: Frame(.zero), typeName: "UILabel")
        #expect(node.labelSource == nil)
        let written = String(decoding: WireJSON.encode(node), as: UTF8.self)
        #expect(written.contains(#""label":null"#) && written.contains(#""label_source":null"#))
        #expect(testNode(children: [testNode(), testNode(children: [testNode()])]).count == 4)
        #expect(
            String(decoding: WireJSON.encode(Modal(kind: .alert)), as: UTF8.self) == #"{"kind":"alert","name":null}"#)
    }

    @Test func framesAreRoundedToTwoDecimalsAndNeverNegativeOrNotANumber() {
        let frame = Frame(CGRect(x: 10.004, y: -3.337, width: 100.555, height: 20))
        #expect(frame == Frame(x: 10, y: -3.34, width: 100.56, height: 20))
        #expect(Frame(x: .nan, y: .infinity, width: -5, height: 1).rect == CGRect(x: 0, y: 0, width: 0, height: 1))
        #expect(Frame(CGRect(x: 10, y: 10, width: -4, height: -4)) == Frame(x: 6, y: 6, width: 4, height: 4))
    }

    @Test func jsonIsWrittenWithSortedKeysAndPlainSlashes() {
        #expect(String(decoding: WireJSON.encode(["b": "a/b", "a": "c"]), as: UTF8.self) == #"{"a":"c","b":"a/b"}"#)
        #expect(nonEmpty("") == nil && nonEmpty(nil) == nil && nonEmpty("x") == "x")
    }
}
