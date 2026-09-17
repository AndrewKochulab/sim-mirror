// SPDX-License-Identifier: Apache-2.0
import Foundation

/// What SimMirror asks the helper, read from a request frame's JSON: ``{"op": "screenshot", "max_width": 900, ...}``.
public enum Request: Equatable, Sendable {
    /// Who the helper is and what it reached: its versions, the device's screen, and how input reaches it.
    case hello
    /// The device's screen.
    case describe
    /// A JPEG of the screen or part of it.
    case screenshot(ScreenshotRequest)
    /// The screen's accessibility document.
    case accessibility
    /// Input events, played in order before the answer.
    case hid([HIDEvent])
    /// The screen as H.264, one chunk an access unit, until the connection closes.
    case stream(StreamSettings)

    /// Whether answering it waits for the answer before the next request on its connection is read: input must stay in
    /// the order it was sent, and a hello is quick.
    public var inOrder: Bool {
        switch self {
        case .hid, .hello: return true
        case .describe, .screenshot, .accessibility, .stream: return false
        }
    }

    public static func decode(_ json: Data) throws -> Request {
        let raw: Raw
        do {
            raw = try JSONDecoder().decode(Raw.self, from: json.isEmpty ? Data("{}".utf8) : json)
        } catch {
            throw HelperFailure("a request that is not one: \(error)", status: 400)
        }
        switch raw.op {
        case "hello": return .hello
        case "describe": return .describe
        case "accessibility": return .accessibility
        case "screenshot":
            return .screenshot(ScreenshotRequest(maxWidth: raw.maxWidth ?? Int.max, quality: raw.quality ?? 75, crop: raw.crop))
        case "hid":
            return .hid(raw.events ?? [])
        case "stream":
            guard let fps = raw.fps, let scale = raw.scale, let keyFrameS = raw.keyFrameS, let bitrate = raw.bitrate else {
                throw HelperFailure("a stream needs fps, scale, key_frame_s and bitrate", status: 400)
            }
            return .stream(StreamSettings(fps: fps, scale: scale, keyFrameS: keyFrameS, bitrate: bitrate))
        default:
            throw HelperFailure("not a request this helper knows: \(raw.op ?? "none")", status: 400)
        }
    }

    private struct Raw: Decodable {
        let op: String?
        let maxWidth: Int?
        let quality: Int?
        let crop: Crop?
        let events: [HIDEvent]?
        let fps: Int?
        let scale: Double?
        let keyFrameS: Double?
        let bitrate: Int?

        enum CodingKeys: String, CodingKey {
            case op, maxWidth = "max_width", quality, crop, events, fps, scale, keyFrameS = "key_frame_s", bitrate
        }
    }
}

/// The answer to a hello.
public struct Hello: Equatable, Sendable, Encodable {
    public let wire: Int
    public let version: String
    public let coreSimulator: String?
    /// The transport input goes through, or nil when input cannot reach the device.
    public let hid: String?
    /// Why a part of the device cannot be reached; empty when all of it can.
    public let reasons: [String]
    public let screen: ScreenGeometry

    public init(coreSimulator: String?, hid: String?, reasons: [String], screen: ScreenGeometry) {
        wire = Wire.version
        version = HelperVersion.current
        self.coreSimulator = coreSimulator
        self.hid = hid
        self.reasons = reasons
        self.screen = screen
    }

    enum CodingKeys: String, CodingKey {
        case wire, version, coreSimulator = "core_simulator", hid, reasons, screen
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(wire, forKey: .wire)
        try container.encode(version, forKey: .version)
        try container.encode(coreSimulator, forKey: .coreSimulator)
        try container.encode(hid, forKey: .hid)
        try container.encode(reasons, forKey: .reasons)
        try container.encode(screen, forKey: .screen)
    }
}

/// The size a JPEG came out at.
struct ImageSize: Encodable {
    let width: Int
    let height: Int
}
