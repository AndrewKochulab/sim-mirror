// SPDX-License-Identifier: Apache-2.0
import Foundation

/// A touch that landed while a device was recorded: when, seconds after the first frame; what; where, each point a
/// fraction of the screen's width and height; for how long; and whose.
public struct RecordedTouch: Equatable, Sendable, Codable {
    public let t: Double
    public let kind: String
    public let points: [[Double]]
    public let duration: Double
    public let by: String

    public init(t: Double, kind: String, points: [[Double]], duration: Double, by: String) {
        self.t = t
        self.kind = kind
        self.points = points
        self.duration = duration
        self.by = by
    }
}

/// What SimMirror asks to be rendered: a recording, the touches to draw on it, and the files to write.
public struct RenderJob: Equatable, Sendable, Codable {
    public enum InputKind: String, Sendable, Codable {
        /// A movie the device's own tool wrote.
        case movie
        /// SimMirror's own frame file (`FrameFile`).
        case frames
    }

    public let input: String
    public let inputKind: InputKind
    public let touches: [RecordedTouch]
    public let mp4: String?
    public let gif: String?
    public let codec: String
    public let speed: Double
    public let gifFps: Int
    public let gifWidth: Int

    public static let speeds = 0.25...8.0
    public static let gifFpsRange = 1...30
    public static let gifWidthRange = 64...2000

    enum CodingKeys: String, CodingKey {
        case input, inputKind = "input_kind", touches, mp4, gif, codec, speed, gifFps = "gif_fps", gifWidth = "gif_width"
    }

    /// A job read from SimMirror's JSON, refused when it asks for something that cannot be made.
    public static func read(_ data: Data) throws -> RenderJob {
        let job: RenderJob
        do {
            job = try JSONDecoder().decode(RenderJob.self, from: data)
        } catch {
            throw HelperFailure("not a render job: \(error)", status: 400)
        }
        guard job.mp4 != nil || job.gif != nil else { throw HelperFailure("a render job names no file to write", status: 400) }
        guard ["h264", "hevc"].contains(job.codec) else { throw HelperFailure("not a codec: \(job.codec)", status: 400) }
        guard speeds.contains(job.speed) else { throw HelperFailure("speed must be 0.25 to 8", status: 400) }
        guard gifFpsRange.contains(job.gifFps) else { throw HelperFailure("gif_fps must be 1 to 30", status: 400) }
        guard gifWidthRange.contains(job.gifWidth) else { throw HelperFailure("gif_width must be 64 to 2000", status: 400) }
        return job
    }
}

/// A file a render wrote.
public struct RenderedFile: Equatable, Sendable, Encodable {
    public let path: String
    public let format: String
    public let width: Int
    public let height: Int
    public let durationS: Double

    public init(path: String, format: String, width: Int, height: Int, durationS: Double) {
        self.path = path
        self.format = format
        self.width = width
        self.height = height
        self.durationS = durationS
    }

    enum CodingKeys: String, CodingKey {
        case path, format, width, height, durationS = "duration_s"
    }
}

public struct RenderReport: Equatable, Sendable, Encodable {
    public let files: [RenderedFile]

    public init(files: [RenderedFile]) {
        self.files = files
    }
}

/// SimMirror's frame file: the frames a screen streamed while it was recorded, each with its time.
///
/// `SMRF` and a version byte, then per frame: its kind (1 an H.264 access unit, 2 a JPEG), its time in seconds after
/// the recording began as a big-endian double, its length as a big-endian 32-bit count, and its bytes.
public enum FrameFile {
    public static let magic = Data("SMRF".utf8) + Data([1])
    public static let headerSize = 13
    public static let largestFrame = 64 << 20

    public enum Kind: UInt8, Sendable {
        case h264 = 1
        case jpeg = 2
    }

    public struct Frame: Equatable, Sendable {
        public let kind: Kind
        public let time: Double
        public let data: Data
    }

    /// The frames a file holds, read a frame at a time by `read(count)`, which answers fewer bytes only at the end.
    public struct Reader {
        let read: (Int) throws -> Data

        public init(read: @escaping (Int) throws -> Data) throws {
            self.read = read
            guard try read(FrameFile.magic.count) == FrameFile.magic else {
                throw HelperFailure("not a SimMirror frame file, or one of a newer version")
            }
        }

        /// The next frame, or nil at the end of the file.
        public mutating func next() throws -> Frame? {
            let header = try read(FrameFile.headerSize)
            if header.isEmpty { return nil }
            guard header.count == FrameFile.headerSize else { throw HelperFailure("a frame file ends inside a frame") }
            let bytes = [UInt8](header)
            guard let kind = Kind(rawValue: bytes[0]) else { throw HelperFailure("a frame of an unknown kind \(bytes[0])") }
            let time = Double(bitPattern: bytes[1..<9].reduce(UInt64(0)) { ($0 << 8) | UInt64($1) })
            let length = Int(bytes[9..<13].reduce(UInt32(0)) { ($0 << 8) | UInt32($1) })
            guard time.isFinite, time >= 0, length > 0, length <= FrameFile.largestFrame else {
                throw HelperFailure("a frame file has a frame of \(length) bytes at \(time)s")
            }
            let data = try read(length)
            guard data.count == length else { throw HelperFailure("a frame file ends inside a frame") }
            return Frame(kind: kind, time: time, data: data)
        }
    }
}

/// When each frame of a rendered file is taken from a recording: a steady rate in the file's own time, each frame the
/// recording's picture at that moment -- the one shown last before it -- so a still screen still shows its touches, and
/// a recording sped up plays shorter.
public struct Ticks: Equatable, Sendable {
    /// Seconds into the rendered file, and seconds into the recording, of each frame.
    public let times: [(output: Double, source: Double)]
    public let fps: Int

    public static func == (a: Ticks, b: Ticks) -> Bool {
        a.fps == b.fps && a.times.elementsEqual(b.times) { $0.output == $1.output && $0.source == $1.source }
    }

    /// The frames of a recording `duration` seconds long, played `speed` times as fast, at `fps` a second.
    public static func make(duration: Double, speed: Double, fps: Int) -> Ticks {
        guard duration > 0, speed > 0, fps > 0 else { return Ticks(times: [(0, 0)], fps: max(fps, 1)) }
        let length = duration / speed
        let count = max(1, Int((length * Double(fps)).rounded(.up)))
        return Ticks(times: (0..<count).map { index in
            let output = Double(index) / Double(fps)
            return (output, min(duration, output * speed))
        }, fps: fps)
    }

    /// How long the rendered file plays.
    public var length: Double { Double(times.count) / Double(fps) }
}

/// The size a rendered file is drawn at: the recording's own, no wider than `width`, both sides even as H.264 wants.
public func renderedSize(sourceWidth: Int, sourceHeight: Int, width: Int?) -> (width: Int, height: Int) {
    guard sourceWidth > 0, sourceHeight > 0 else { return (2, 2) }
    let wide = min(sourceWidth, width ?? sourceWidth)
    let high = Double(sourceHeight) * Double(wide) / Double(sourceWidth)
    return (EncoderPlan.even(Double(wide)), EncoderPlan.even(high))
}

/// What is drawn over a frame to show a touch: a ring where a finger is or has just been, and the path a moving one
/// has taken. Positions are fractions of the frame's width and height, measured from its top left.
public struct TouchMark: Equatable, Sendable {
    public enum Shape: Equatable, Sendable {
        case ring(x: Double, y: Double, radius: Double)
        case trail([[Double]])
    }

    public let shape: Shape
    /// How opaque it is, 0 to 1: a touch fades once it lifts.
    public let alpha: Double
    public let byAgent: Bool
}

/// Which marks show at a moment of a recording.
public enum TouchMarks {
    /// How long a lifted touch takes to fade.
    public static let fade = 0.35
    /// How long the shortest touch shows, so a tap is seen at all.
    public static let shortest = 0.15
    /// A ring's radius, as a fraction of the frame's width; a long press grows it by half.
    public static let radius = 0.045

    public static func at(_ time: Double, _ touches: [RecordedTouch]) -> [TouchMark] {
        touches.flatMap { marks(of: $0, at: time) }
    }

    static func marks(of touch: RecordedTouch, at time: Double) -> [TouchMark] {
        let points = touch.points.filter { $0.count == 2 }
        guard let first = points.first else { return [] }
        let held = max(touch.duration, shortest)
        let since = time - touch.t
        guard since >= 0, since <= held + fade else { return [] }
        let alpha = since <= held ? 1 : max(0, 1 - (since - held) / fade)
        let agent = touch.by == "agent"
        guard points.count > 1 else {
            let grow = touch.kind == "long_press" ? 1 + 0.5 * min(1, since / held) : 1
            return [TouchMark(shape: .ring(x: first[0], y: first[1], radius: radius * grow), alpha: alpha, byAgent: agent)]
        }
        let travelled = min(1, since / held)
        let (path, finger) = along(points, travelled)
        return [
            TouchMark(shape: .trail(path), alpha: alpha * 0.8, byAgent: agent),
            TouchMark(shape: .ring(x: finger[0], y: finger[1], radius: radius), alpha: alpha, byAgent: agent),
        ]
    }

    /// The part of a path a finger has covered at a fraction of the way along it, and where the finger is.
    static func along(_ points: [[Double]], _ fraction: Double) -> ([[Double]], [Double]) {
        let legs = zip(points, points.dropFirst()).map { hypot($1[0] - $0[0], $1[1] - $0[1]) }
        let total = legs.reduce(0, +)
        guard total > 0 else { return ([points[0]], points[0]) }
        var left = total * fraction
        var path = [points[0]]
        for (index, leg) in legs.enumerated() {
            let (from, to) = (points[index], points[index + 1])
            if left <= leg {
                let part = leg > 0 ? left / leg : 0
                let finger = [from[0] + (to[0] - from[0]) * part, from[1] + (to[1] - from[1]) * part]
                path.append(finger)
                return (path, finger)
            }
            left -= leg
            path.append(to)
        }
        return (path, points[points.count - 1])
    }
}
