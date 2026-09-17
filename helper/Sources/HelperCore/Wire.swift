// SPDX-License-Identifier: Apache-2.0
import Foundation

/// The helper's version, which is SimMirror's: SimMirror refuses a helper of another version.
public enum HelperVersion {
    public static let current = "1.2.0"
}

/// What a frame on the socket is.
public enum FrameKind: UInt8, Sendable, CaseIterable {
    /// SimMirror asks for something.
    case request = 1
    /// The answer to a request.
    case reply = 2
    /// A request that did not work, and why.
    case failure = 3
    /// One piece of a stream a request started: an H.264 access unit.
    case chunk = 4
}

/// One message on the socket: a kind, the request it belongs to, a JSON object and any bytes that follow it.
///
/// On the wire, every number is big-endian:
///
///     u32 length     bytes after this field
///     u8  kind
///     u32 id         the request this frame asks or answers
///     u32 json size
///     json           a UTF-8 JSON object; empty means {}
///     blob           the rest: a JPEG, an access unit, or nothing
public struct Frame: Equatable, Sendable {
    public var kind: FrameKind
    public var id: UInt32
    public var json: Data
    public var blob: Data

    public init(kind: FrameKind, id: UInt32, json: Data = Data(), blob: Data = Data()) {
        self.kind = kind
        self.id = id
        self.json = json
        self.blob = blob
    }
}

/// Why bytes on the socket are not a frame.
public enum WireError: Error, Equatable, CustomStringConvertible {
    case tooLarge(Int)
    case unknownKind(UInt8)
    case jsonOverruns(Int)

    public var description: String {
        switch self {
        case .tooLarge(let size): return "a frame of \(size) bytes is larger than \(Wire.maxFrame)"
        case .unknownKind(let kind): return "frame kind \(kind) is not one this helper knows"
        case .jsonOverruns(let size): return "a frame's JSON of \(size) bytes runs past its end"
        }
    }
}

public enum Wire {
    /// The protocol's version, which SimMirror checks in the hello.
    public static let version = 1
    /// The bytes after the length a frame has before its JSON: kind, id and JSON size.
    static let headerAfterLength = 9
    /// The largest frame either side accepts.
    public static let maxFrame = 64 << 20

    public static func encode(_ frame: Frame) -> Data {
        var out = Data(capacity: 4 + headerAfterLength + frame.json.count + frame.blob.count)
        append(UInt32(headerAfterLength + frame.json.count + frame.blob.count), to: &out)
        out.append(frame.kind.rawValue)
        append(frame.id, to: &out)
        append(UInt32(frame.json.count), to: &out)
        out.append(frame.json)
        out.append(frame.blob)
        return out
    }

    static func append(_ value: UInt32, to data: inout Data) {
        withUnsafeBytes(of: value.bigEndian) { data.append(contentsOf: $0) }
    }

    static func uint32(_ data: Data, at offset: Int) -> UInt32 {
        let start = data.startIndex + offset
        return data[start..<start + 4].reduce(0) { ($0 << 8) | UInt32($1) }
    }
}

/// Frames out of a byte stream that arrives in whatever pieces the socket gives.
public struct FrameDecoder: Sendable {
    private var buffer = Data()

    public init() {}

    /// The frames `bytes` completes, in order. Throws when the stream is not frames, after which it cannot be read on.
    public mutating func push(_ bytes: Data) throws -> [Frame] {
        buffer.append(bytes)
        var frames: [Frame] = []
        while buffer.count >= 4 {
            let length = Int(Wire.uint32(buffer, at: 0))
            guard length >= Wire.headerAfterLength, length <= Wire.maxFrame else { throw WireError.tooLarge(length) }
            guard buffer.count >= 4 + length else { break }
            let rawKind = buffer[buffer.startIndex + 4]
            guard let kind = FrameKind(rawValue: rawKind) else { throw WireError.unknownKind(rawKind) }
            let id = Wire.uint32(buffer, at: 5)
            let jsonSize = Int(Wire.uint32(buffer, at: 9))
            guard jsonSize <= length - Wire.headerAfterLength else { throw WireError.jsonOverruns(jsonSize) }
            let jsonStart = buffer.startIndex + 13
            let end = buffer.startIndex + 4 + length
            frames.append(
                Frame(
                    kind: kind,
                    id: id,
                    json: Data(buffer[jsonStart..<jsonStart + jsonSize]),
                    blob: Data(buffer[jsonStart + jsonSize..<end])
                )
            )
            buffer = Data(buffer[end...])
        }
        return frames
    }
}
