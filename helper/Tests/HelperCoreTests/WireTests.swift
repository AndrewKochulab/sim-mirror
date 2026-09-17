// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

/// The frames SimMirror's Python side is tested against too, so the two cannot drift apart.
struct WireVectors: Decodable {
    struct Vector: Decodable {
        let name: String
        let kind: UInt8
        let id: UInt32
        let json: String
        let blob: String
        let hex: String
    }

    let wire: Int
    let frames: [Vector]

    static func load() throws -> WireVectors {
        let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Fixtures/wire-vectors.json")
        return try JSONDecoder().decode(WireVectors.self, from: Data(contentsOf: url))
    }
}

func bytes(_ hex: String) -> Data {
    var data = Data()
    var index = hex.startIndex
    while index < hex.endIndex {
        let next = hex.index(index, offsetBy: 2)
        data.append(UInt8(hex[index..<next], radix: 16)!)
        index = next
    }
    return data
}

@Suite struct WireTests {
    @Test func aFrameIsItsLengthKindIdJsonSizeJsonAndBlob() {
        let frame = Frame(kind: .reply, id: 7, json: Data("{}".utf8), blob: Data([0xFF, 0xD8]))
        #expect([UInt8](Wire.encode(frame)) == [0, 0, 0, 13, 2, 0, 0, 0, 7, 0, 0, 0, 2, 0x7B, 0x7D, 0xFF, 0xD8])
    }

    @Test func everySharedVectorEncodesAndDecodesToTheSameBytes() throws {
        let vectors = try WireVectors.load()
        #expect(vectors.wire == Wire.version)
        #expect(vectors.frames.count >= FrameKind.allCases.count)
        for vector in vectors.frames {
            let frame = Frame(kind: FrameKind(rawValue: vector.kind)!, id: vector.id, json: Data(vector.json.utf8), blob: bytes(vector.blob))
            #expect(Wire.encode(frame) == bytes(vector.hex), "\(vector.name)")
            var decoder = FrameDecoder()
            #expect(try decoder.push(bytes(vector.hex)) == [frame], "\(vector.name)")
        }
    }

    @Test func framesArriveWhateverPiecesTheSocketCutsThemInto() throws {
        let vectors = try WireVectors.load()
        let stream = vectors.frames.reduce(Data()) { $0 + bytes($1.hex) }
        var decoder = FrameDecoder()
        var frames: [Frame] = []
        for byte in stream {
            frames += try decoder.push(Data([byte]))
        }
        #expect(frames.map(\.id) == vectors.frames.map(\.id))
        #expect(try decoder.push(Data()) == [])
    }

    @Test func aStreamThatIsNotFramesIsRefused() {
        var tooSmall = FrameDecoder()
        #expect(throws: WireError.tooLarge(2)) { try tooSmall.push(Data([0, 0, 0, 2])) }
        var tooLarge = FrameDecoder()
        #expect(throws: WireError.tooLarge(Wire.maxFrame + 1)) {
            var length = Data()
            Wire.append(UInt32(Wire.maxFrame + 1), to: &length)
            _ = try tooLarge.push(length)
        }
        var unknown = FrameDecoder()
        #expect(throws: WireError.unknownKind(9)) { try unknown.push(Data([0, 0, 0, 9, 9, 0, 0, 0, 1, 0, 0, 0, 0])) }
        var overrun = FrameDecoder()
        #expect(throws: WireError.jsonOverruns(5)) { try overrun.push(Data([0, 0, 0, 9, 1, 0, 0, 0, 1, 0, 0, 0, 5])) }
        #expect(WireError.tooLarge(3).description.contains("3 bytes"))
        #expect(WireError.unknownKind(9).description.contains("kind 9"))
        #expect(WireError.jsonOverruns(5).description.contains("5 bytes"))
    }

    @Test func theHelperIsSimMirrorsVersion() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let python = try String(contentsOf: root.appendingPathComponent("src/sim_mirror/_version.py"), encoding: .utf8)
        #expect(python.contains("\"\(HelperVersion.current)\""))
    }
}

@Suite struct JSONTests {
    @Test func valuesEncodeWithSortedKeysAndNulls() {
        let value = JSONValue.object([
            "b": .array([.null, .bool(true), .int(3), .number(1.5), .string("a/b")]),
            "a": .text(nil),
            "c": .text("x"),
        ])
        #expect(String(decoding: JSON.encode(value), as: UTF8.self) == #"{"a":null,"b":[null,true,3,1.5,"a/b"],"c":"x"}"#)
    }

    @Test func anObjectKeyIsOnlyText() {
        #expect(JSONValue.Key(stringValue: "a")?.stringValue == "a")
        #expect(JSONValue.Key("b").intValue == nil)
        #expect(JSONValue.Key(intValue: 1) == nil)
    }

    @Test func numbersThatAreNotNumbersAreText() {
        #expect(String(decoding: JSON.encode([Double.infinity, -Double.infinity]), as: UTF8.self) == #"["inf","-inf"]"#)
    }

    @Test func aFailureIsItsMessageAndStatus() {
        let failure = HelperFailure("gone", status: 409)
        #expect(failure.description == "gone")
        #expect(HelperFailure.from(failure) == failure)
        #expect(HelperFailure.from(WireError.unknownKind(7)) == HelperFailure("frame kind 7 is not one this helper knows"))
        #expect(String(decoding: JSON.encode(failure), as: UTF8.self) == #"{"message":"gone","status":409}"#)
    }
}
