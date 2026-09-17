// SPDX-License-Identifier: Apache-2.0
import Foundation

/// A JPEG and the size it came out at.
public struct JPEG: Equatable, Sendable {
    public let data: Data
    public let width: Int
    public let height: Int

    public init(data: Data, width: Int, height: Int) {
        self.data = data
        self.width = width
        self.height = height
    }
}

/// A booted device, as the helper reaches it. `HelperPlatform` is the real one; tests give their own.
public protocol Device: AnyObject, Sendable {
    /// The version of CoreSimulator this process loaded, which is what decides how input reaches a device.
    var coreSimulator: String? { get }
    func screen() throws -> ScreenGeometry
    func screenshot(_ request: ScreenshotRequest) async throws -> JPEG
    func tree() async throws -> AXNode
    /// How input reaches the device, opened the first time it is asked for.
    func input() throws -> InputDriver
    /// The screen as H.264 access units, until the stream is let go of.
    func stream(_ settings: StreamSettings) throws -> AsyncThrowingStream<Data, Error>
    /// Returns once what the first screenshot and stream need is open, so a hello answered after it means the device's
    /// first picture is quick.
    func warmed() async
}

/// Bytes to and from one peer: a connection's socket, or a test's stand-in.
public protocol ByteChannel: Sendable {
    /// The next bytes to arrive, or nil when the peer has closed.
    func read() async throws -> Data?
    func write(_ data: Data) async throws
    func close()
}

/// Answers requests on a device.
public final class Router: Sendable {
    let device: Device

    public init(device: Device) {
        self.device = device
    }

    /// Answer one request, sending its reply, its chunks or why it failed. Never throws: a peer that has gone away
    /// just misses the answer.
    public func answer(_ id: UInt32, _ request: Request, send: @Sendable (Frame) async throws -> Void) async {
        do {
            switch request {
            case .hello:
                await device.warmed()
                try await send(Frame(kind: .reply, id: id, json: JSON.encode(try hello())))
            case .describe:
                try await send(Frame(kind: .reply, id: id, json: JSON.encode(try device.screen())))
            case .screenshot(let wanted):
                let jpeg = try await device.screenshot(wanted)
                try await send(
                    Frame(kind: .reply, id: id, json: JSON.encode(ImageSize(width: jpeg.width, height: jpeg.height)), blob: jpeg.data)
                )
            case .accessibility:
                let root = try await device.tree()
                try await send(Frame(kind: .reply, id: id, json: JSON.encode(AXDocument.make(root: root, screen: try device.screen()))))
            case .hid(let events):
                try device.input().play(events)
                try await send(Frame(kind: .reply, id: id, json: Data("{}".utf8)))
            case .stream(let settings):
                for try await unit in try device.stream(settings) {
                    try await send(Frame(kind: .chunk, id: id, blob: unit))
                }
                try await send(Frame(kind: .reply, id: id, json: Data("{}".utf8)))
            }
        } catch {
            try? await send(Frame(kind: .failure, id: id, json: JSON.encode(HelperFailure.from(error))))
        }
    }

    func hello() throws -> Hello {
        let screen = try device.screen()
        do {
            return Hello(coreSimulator: device.coreSimulator, hid: try device.input().transport.name, reasons: [], screen: screen)
        } catch {
            return Hello(coreSimulator: device.coreSimulator, hid: nil, reasons: [HelperFailure.from(error).message], screen: screen)
        }
    }
}

/// Sends frames on a channel one at a time, so frames from requests answered at once never interleave.
public actor FrameWriter {
    let channel: ByteChannel

    public init(channel: ByteChannel) {
        self.channel = channel
    }

    public func send(_ frame: Frame) async throws {
        try await channel.write(Wire.encode(frame))
    }
}

/// One peer's requests, read until it closes: input and hellos answered in order, the rest at once.
public final class Connection: Sendable {
    let channel: ByteChannel
    let router: Router
    let log: Log

    public init(channel: ByteChannel, router: Router, log: Log = Log()) {
        self.channel = channel
        self.router = router
        self.log = log
    }

    public func run() async {
        let writer = FrameWriter(channel: channel)
        let send: @Sendable (Frame) async throws -> Void = { try await writer.send($0) }
        var decoder = FrameDecoder()
        await withTaskGroup(of: Void.self) { group in
            reading: while true {
                guard let bytes = try? await channel.read() else { break }
                let frames: [Frame]
                do {
                    frames = try decoder.push(bytes)
                } catch {
                    log.warning("closing a connection that sent what is not frames: \(error)")
                    break reading
                }
                for frame in frames where frame.kind == .request {
                    let request: Request
                    do {
                        request = try Request.decode(frame.json)
                    } catch {
                        try? await send(Frame(kind: .failure, id: frame.id, json: JSON.encode(HelperFailure.from(error))))
                        continue
                    }
                    if request.inOrder {
                        await router.answer(frame.id, request, send: send)
                    } else {
                        group.addTask { await self.router.answer(frame.id, request, send: send) }
                    }
                }
            }
            group.cancelAll()
        }
        channel.close()
    }
}

/// Lines for the helper's log, each with its level; the log folder is SimMirror's.
public struct Log: Sendable {
    public enum Level: Int, Sendable, Comparable {
        case debug, info, warning, error

        public static func < (lhs: Level, rhs: Level) -> Bool { lhs.rawValue < rhs.rawValue }

        public init?(name: String) {
            switch name {
            case "debug": self = .debug
            case "info": self = .info
            case "warning": self = .warning
            case "error": self = .error
            default: return nil
            }
        }
    }

    public let level: Level
    let write: @Sendable (String) -> Void

    public init(level: Level = .info, write: @escaping @Sendable (String) -> Void = { FileHandle.standardError.write(Data(($0 + "\n").utf8)) }) {
        self.level = level
        self.write = write
    }

    public func debug(_ message: @autoclosure () -> String) { say(.debug, message()) }
    public func info(_ message: @autoclosure () -> String) { say(.info, message()) }
    public func warning(_ message: @autoclosure () -> String) { say(.warning, message()) }
    public func error(_ message: @autoclosure () -> String) { say(.error, message()) }

    private func say(_ at: Level, _ message: @autoclosure () -> String) {
        guard at >= level else { return }
        write("[\(at)] \(message())")
    }
}
