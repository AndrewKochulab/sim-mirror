// SPDX-License-Identifier: Apache-2.0
import Foundation

/// What a stream asks for: frames a second, the fraction of the screen's width to encode at, how often a key frame
/// comes, and the average bitrate.
public struct StreamSettings: Equatable, Sendable, Codable {
    public let fps: Int
    public let scale: Double
    public let keyFrameS: Double
    public let bitrate: Int

    public init(fps: Int, scale: Double, keyFrameS: Double, bitrate: Int) {
        self.fps = fps
        self.scale = scale
        self.keyFrameS = keyFrameS
        self.bitrate = bitrate
    }

    enum CodingKeys: String, CodingKey {
        case fps, scale, keyFrameS = "key_frame_s", bitrate
    }
}

/// How an encoder is set up for a stream on a framebuffer.
public struct EncoderPlan: Equatable, Sendable {
    public let width: Int
    public let height: Int
    public let fps: Int
    /// Seconds between key frames at most.
    public let keyFrameInterval: Double
    public let bitrate: Int
    /// The most bytes a second may carry: half again the average, so a burst of motion is not starved.
    public let bytesPerSecondLimit: Int

    public static let fpsRange = 1...120
    public static let minimumBitrate = 100_000

    public static func make(_ settings: StreamSettings, surfaceWidth: Int, surfaceHeight: Int) throws -> EncoderPlan {
        guard surfaceWidth > 0, surfaceHeight > 0 else { throw HelperFailure("the screen has no picture yet", status: 503) }
        guard fpsRange.contains(settings.fps) else { throw HelperFailure("fps must be 1 to 120", status: 400) }
        guard settings.scale.isFinite, settings.scale > 0, settings.scale <= 1 else {
            throw HelperFailure("scale must be more than 0 and at most 1", status: 400)
        }
        guard settings.keyFrameS.isFinite, settings.keyFrameS > 0 else {
            throw HelperFailure("key_frame_s must be more than 0", status: 400)
        }
        guard settings.bitrate >= minimumBitrate else {
            throw HelperFailure("bitrate must be at least \(minimumBitrate)", status: 400)
        }
        return EncoderPlan(
            width: even(Double(surfaceWidth) * settings.scale),
            height: even(Double(surfaceHeight) * settings.scale),
            fps: settings.fps,
            keyFrameInterval: settings.keyFrameS,
            bitrate: settings.bitrate,
            bytesPerSecondLimit: settings.bitrate * 3 / 16
        )
    }

    /// H.264 needs even dimensions; the nearest even number no smaller than 2.
    static func even(_ value: Double) -> Int {
        max(2, Int((value / 2).rounded()) * 2)
    }
}

/// H.264 as SimMirror streams it: Annex-B access units, a key frame carrying its parameter sets, each unit closed by an
/// access unit delimiter.
///
/// VideoToolbox answers AVCC: each NAL unit after its length. A viewer reads Annex-B: each after a start code, and a
/// key frame it can begin at carries the sequence and picture parameter sets. The delimiter after each unit is what
/// lets a reader know the picture is whole the moment it arrives, rather than when the next one starts.
public enum AnnexB {
    public static let startCode: [UInt8] = [0, 0, 0, 1]
    /// An access unit delimiter NAL unit, saying any picture type may follow.
    public static let accessUnitDelimiter: [UInt8] = [0, 0, 0, 1, 0x09, 0xF0]

    /// One access unit in Annex-B, from AVCC NAL units of `lengthSize` bytes each, with `parameterSets` before them.
    public static func accessUnit(avcc: Data, lengthSize: Int = 4, parameterSets: [Data] = []) throws -> Data {
        guard (1...4).contains(lengthSize) else { throw HelperFailure("a NAL length of \(lengthSize) bytes") }
        var out = Data(capacity: avcc.count + parameterSets.reduce(0) { $0 + $1.count + 4 } + 16)
        for set in parameterSets {
            out.append(contentsOf: startCode)
            out.append(set)
        }
        var at = avcc.startIndex
        while at < avcc.endIndex {
            guard avcc.endIndex - at >= lengthSize else { throw HelperFailure("an encoded frame ends inside a NAL length") }
            let length = avcc[at..<at + lengthSize].reduce(0) { ($0 << 8) | Int($1) }
            at += lengthSize
            guard length > 0, avcc.endIndex - at >= length else {
                throw HelperFailure("an encoded frame has a NAL unit of \(length) bytes that it does not hold")
            }
            out.append(contentsOf: startCode)
            out.append(avcc[at..<at + length])
            at += length
        }
        out.append(contentsOf: accessUnitDelimiter)
        return out
    }
}
