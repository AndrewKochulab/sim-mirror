// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

final class FakeDevice: Device, @unchecked Sendable {
    let coreSimulator: String? = "1171.7"
    var screenFailure: HelperFailure?
    var inputFailure: HelperFailure?
    var treeFailure: HelperFailure?
    var shotFailure: HelperFailure?
    var units: [Data] = [Data([0, 0, 0, 1, 0x67]), Data([0, 0, 0, 1, 0x41])]
    var streamFailure: HelperFailure?
    let transport = RecordingTransport()
    private(set) lazy var driver = InputDriver(transport: transport, screen: iPhone17)
    var shots: [ScreenshotRequest] = []
    var warmings = 0

    func screen() throws -> ScreenGeometry {
        if let screenFailure { throw screenFailure }
        return iPhone17
    }

    func screenshot(_ request: ScreenshotRequest) async throws -> JPEG {
        if let shotFailure { throw shotFailure }
        shots.append(request)
        return JPEG(data: Data([0xFF, 0xD8]), width: 90, height: 196)
    }

    func tree() async throws -> AXNode {
        if let treeFailure { throw treeFailure }
        return AXNode(role: "AXApplication", label: "Home", children: [AXNode(role: "AXButton", label: "Maps")])
    }

    func input() throws -> InputDriver {
        if let inputFailure { throw inputFailure }
        return driver
    }

    func warmed() async {
        warmings += 1
    }

    func stream(_ settings: StreamSettings) throws -> AsyncThrowingStream<Data, Error> {
        if settings.fps == 0 { throw HelperFailure("fps must be 1 to 120", status: 400) }
        let units = self.units
        let failure = streamFailure
        return AsyncThrowingStream { continuation in
            for unit in units { continuation.yield(unit) }
            if let failure { continuation.finish(throwing: failure) } else { continuation.finish() }
        }
    }
}

actor Sent {
    var frames: [Frame] = []
    func add(_ frame: Frame) { frames.append(frame) }
}

func text(_ frame: Frame) -> String {
    String(decoding: frame.json, as: UTF8.self)
}

@Suite struct RequestTests {
    @Test func everyOpIsRead() throws {
        #expect(try Request.decode(Data(#"{"op":"hello"}"#.utf8)) == .hello)
        #expect(try Request.decode(Data(#"{"op":"describe"}"#.utf8)) == .describe)
        #expect(try Request.decode(Data(#"{"op":"accessibility"}"#.utf8)) == .accessibility)
        #expect(try Request.decode(Data(#"{"op":"screenshot"}"#.utf8)) == .screenshot(ScreenshotRequest(maxWidth: Int.max, quality: 75)))
        #expect(try Request.decode(Data(#"{"op":"hid"}"#.utf8)) == .hid([]))
        #expect(try Request.decode(Data(#"{"op":"stream","fps":30,"scale":1,"key_frame_s":1,"bitrate":200000}"#.utf8))
            == .stream(StreamSettings(fps: 30, scale: 1, keyFrameS: 1, bitrate: 200_000)))
    }

    @Test func theSharedVectorsReadAsTheRequestsTheyName() throws {
        let requests = try WireVectors.load().frames.filter { $0.kind == FrameKind.request.rawValue }.map { try Request.decode(Data($0.json.utf8)) }
        #expect(requests == [
            .hello,
            .screenshot(ScreenshotRequest(maxWidth: 900, quality: 75, crop: Crop(x: 1.5, y: 2, width: 10, height: 20))),
            .hid([HIDEvent(kind: .touch, phase: .down, x: 120.5, y: 406)]),
            .stream(StreamSettings(fps: 30, scale: 0.75, keyFrameS: 1, bitrate: 3_000_000)),
        ])
    }

    @Test func whatIsNotARequestSaysWhy() {
        #expect(throws: HelperFailure("not a request this helper knows: none", status: 400)) { try Request.decode(Data()) }
        #expect(throws: HelperFailure("not a request this helper knows: jump", status: 400)) { try Request.decode(Data(#"{"op":"jump"}"#.utf8)) }
        #expect(throws: HelperFailure("a stream needs fps, scale, key_frame_s and bitrate", status: 400)) { try Request.decode(Data(#"{"op":"stream"}"#.utf8)) }
        #expect(throws: HelperFailure.self) { try Request.decode(Data("[".utf8)) }
    }

    @Test func onlyInputAndHellosAreAnsweredInOrder() {
        #expect(Request.hid([]).inOrder && Request.hello.inOrder)
        #expect(![Request.describe, .accessibility, .screenshot(ScreenshotRequest(maxWidth: 1, quality: 1)), .stream(StreamSettings(fps: 1, scale: 1, keyFrameS: 1, bitrate: 1))].contains { $0.inOrder })
    }
}

@Suite struct RouterTests {
    func answer(_ request: Request, on device: FakeDevice = FakeDevice()) async -> [Frame] {
        let sent = Sent()
        await Router(device: device).answer(9, request) { await sent.add($0) }
        return await sent.frames
    }

    @Test func aHelloSaysTheVersionsTheScreenAndHowInputGoes() async {
        let frames = await answer(.hello)
        #expect(frames.count == 1 && frames[0].kind == .reply && frames[0].id == 9)
        #expect(text(frames[0]) == #"{"core_simulator":"1171.7","hid":"dtuhid","reasons":[],"screen":{"height_pt":874,"height_px":2622,"scale":3,"width_pt":402,"width_px":1206},"version":"\#(HelperVersion.current)","wire":1}"#)
        let device = FakeDevice()
        device.inputFailure = HelperFailure("no digitizer")
        #expect(text(await answer(.hello, on: device)[0]).contains(#""hid":null,"reasons":["no digitizer"]"#))
        #expect(device.warmings == 1)
    }

    @Test func describeScreenshotTreeAndInputAreAnswered() async {
        let device = FakeDevice()
        #expect(text(await answer(.describe, on: device)[0]).hasPrefix(#"{"height_pt":874"#))
        let shot = await answer(.screenshot(ScreenshotRequest(maxWidth: 90, quality: 50)), on: device)
        #expect(text(shot[0]) == #"{"height":196,"width":90}"# && shot[0].blob == Data([0xFF, 0xD8]) && device.shots.count == 1)
        #expect(text(await answer(.accessibility, on: device)[0]).contains(#""label":"Maps""#))
        let input = await answer(.hid([HIDEvent(kind: .key, phase: .down, code: 4)]), on: device)
        #expect(input[0].kind == .reply && text(input[0]) == "{}" && device.transport.sent == [.key(4, down: true)])
    }

    @Test func aStreamIsItsChunksThenAReply() async {
        let frames = await answer(.stream(StreamSettings(fps: 30, scale: 1, keyFrameS: 1, bitrate: 1)))
        #expect(frames.map(\.kind) == [.chunk, .chunk, .reply])
        #expect(frames[0].blob == Data([0, 0, 0, 1, 0x67]))
    }

    @Test func whatFailsIsAnsweredWithWhy() async {
        let device = FakeDevice()
        device.screenFailure = HelperFailure("no screen", status: 409)
        #expect(await answer(.describe, on: device) == [Frame(kind: .failure, id: 9, json: Data(#"{"message":"no screen","status":409}"#.utf8))])
        #expect(await answer(.hello, on: device)[0].kind == .failure)
        let other = FakeDevice()
        other.shotFailure = HelperFailure("dark")
        other.treeFailure = HelperFailure("no app")
        other.inputFailure = HelperFailure("no input")
        other.streamFailure = HelperFailure("encoder")
        #expect(await answer(.screenshot(ScreenshotRequest(maxWidth: 1, quality: 1)), on: other)[0].kind == .failure)
        #expect(await answer(.accessibility, on: other)[0].kind == .failure)
        #expect(await answer(.hid([]), on: other)[0].kind == .failure)
        #expect(await answer(.stream(StreamSettings(fps: 30, scale: 1, keyFrameS: 1, bitrate: 1)), on: other).map(\.kind) == [.chunk, .chunk, .failure])
        #expect(await answer(.stream(StreamSettings(fps: 0, scale: 1, keyFrameS: 1, bitrate: 1)), on: other).map(\.kind) == [.failure])
    }

    @Test func aPeerThatHasGoneMissesTheAnswerQuietly() async {
        await Router(device: FakeDevice()).answer(1, .describe) { _ in throw HelperFailure("closed") }
    }
}

final class FakeChannel: ByteChannel, @unchecked Sendable {
    private let lock = NSLock()
    private var incoming: [Data]
    private let failRead: Bool
    private(set) var written = Data()
    private(set) var closed = false

    init(_ incoming: [Data], failRead: Bool = false) {
        self.incoming = incoming
        self.failRead = failRead
    }

    func read() async throws -> Data? {
        await Task.yield()
        let next: Data? = lock.withLock { incoming.isEmpty ? nil : incoming.removeFirst() }
        if next == nil, failRead { throw HelperFailure("reset") }
        return next
    }

    func write(_ data: Data) async throws {
        lock.withLock { written.append(data) }
    }

    func close() {
        closed = true
    }

    func frames() throws -> [Frame] {
        var decoder = FrameDecoder()
        return try decoder.push(written)
    }
}

@Suite struct ConnectionTests {
    func request(_ id: UInt32, _ json: String) -> Data {
        Wire.encode(Frame(kind: .request, id: id, json: Data(json.utf8)))
    }

    @Test func requestsAreAnsweredAndTheChannelClosedWhenThePeerCloses() async throws {
        let channel = FakeChannel([
            request(1, #"{"op":"hello"}"#) + request(2, #"{"op":"describe"}"#),
            Wire.encode(Frame(kind: .reply, id: 3)),
            request(4, #"{"op":"jump"}"#),
            request(5, #"{"op":"hid","events":[{"kind":"key","phase":"down","code":4}]}"#),
        ])
        let lines = LockedLines()
        await Connection(channel: channel, router: Router(device: FakeDevice()), log: Log(level: .debug, write: lines.add)).run()
        let frames = try channel.frames()
        #expect(Set(frames.map(\.id)) == [1, 2, 4, 5] && channel.closed)
        #expect(frames.first { $0.id == 4 }?.kind == .failure)
    }

    @Test func aPeerThatSendsWhatIsNotFramesIsLetGo() async {
        let channel = FakeChannel([Data([0, 0, 0, 1, 9])])
        let lines = LockedLines()
        await Connection(channel: channel, router: Router(device: FakeDevice()), log: Log(level: .warning, write: lines.add)).run()
        #expect(channel.closed && lines.all.count == 1 && lines.all[0].hasPrefix("[warning] closing a connection"))
    }

    @Test func aReadThatFailsEndsTheConnection() async {
        let channel = FakeChannel([], failRead: true)
        await Connection(channel: channel, router: Router(device: FakeDevice())).run()
        #expect(channel.closed)
    }
}

final class LockedLines: @unchecked Sendable {
    private let lock = NSLock()
    private var lines: [String] = []

    var all: [String] {
        lock.lock()
        defer { lock.unlock() }
        return lines
    }

    func add(_ line: String) {
        lock.lock()
        lines.append(line)
        lock.unlock()
    }
}

@Suite struct LogTests {
    @Test func linesBelowTheLevelAreNotWritten() {
        let lines = LockedLines()
        let log = Log(level: .info, write: lines.add)
        log.debug("quiet")
        log.info("one")
        log.warning("two")
        log.error("three")
        #expect(lines.all == ["[info] one", "[warning] two", "[error] three"])
        #expect(Log.Level(name: "debug") == .debug && Log.Level(name: "error") == .error && Log.Level(name: "loud") == nil)
        #expect(Log().level == .info)
    }
}
