// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

@Suite struct RecordingTests {
    static let job = #"{"input":"/r/.a.raw","input_kind":"frames","touches":[{"t":1.5,"kind":"tap","points":[[0.5,0.25]],"duration":0.1,"by":"agent"}],"mp4":"/r/a.mp4","gif":null,"codec":"h264","speed":2,"gif_fps":12,"gif_width":600}"#

    @Test func aJobIsReadWithEverythingItAsks() throws {
        let job = try RenderJob.read(Data(Self.job.utf8))
        #expect(job.input == "/r/.a.raw" && job.inputKind == .frames && job.mp4 == "/r/a.mp4" && job.gif == nil)
        #expect(job.codec == "h264" && job.speed == 2 && job.gifFps == 12 && job.gifWidth == 600)
        #expect(job.touches == [RecordedTouch(t: 1.5, kind: "tap", points: [[0.5, 0.25]], duration: 0.1, by: "agent")])
    }

    @Test(arguments: [
        ("{", "not a render job"),
        (#"{"mp4":null,"gif":null}"#, "not a render job"),
        (job.replacingOccurrences(of: #""mp4":"/r/a.mp4""#, with: #""mp4":null"#), "names no file to write"),
        (job.replacingOccurrences(of: "h264", with: "prores"), "not a codec: prores"),
        (job.replacingOccurrences(of: #""speed":2"#, with: #""speed":20"#), "speed must be 0.25 to 8"),
        (job.replacingOccurrences(of: #""gif_fps":12"#, with: #""gif_fps":60"#), "gif_fps must be 1 to 30"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":20"#), "gif_width must be 64 to 2000"),
    ] as [(String, String)])
    func aJobThatCannotBeMadeIsRefused(json: String, says: String) {
        do {
            _ = try RenderJob.read(Data(json.utf8))
            Issue.record("\(json) was read")
        } catch {
            #expect((error as? HelperFailure)?.message.contains(says) == true)
        }
    }

    @Test func aReportSaysWhatWasWritten() {
        let report = RenderReport(files: [RenderedFile(path: "/r/a.mp4", format: "mp4", width: 400, height: 868, durationS: 2.5)])
        #expect(String(decoding: JSON.encode(report), as: UTF8.self) == #"{"files":[{"duration_s":2.5,"format":"mp4","height":868,"path":"/r/a.mp4","width":400}]}"#)
    }

    static func frame(_ kind: UInt8, _ time: Double, _ body: [UInt8]) -> Data {
        var data = Data([kind])
        withUnsafeBytes(of: time.bitPattern.bigEndian) { data.append(contentsOf: $0) }
        withUnsafeBytes(of: UInt32(body.count).bigEndian) { data.append(contentsOf: $0) }
        data.append(contentsOf: body)
        return data
    }

    static func reader(_ data: Data) throws -> FrameFile.Reader {
        var at = data.startIndex
        return try FrameFile.Reader { count in
            let end = min(data.endIndex, at + count)
            defer { at = end }
            return data[at..<end]
        }
    }

    @Test func aFrameFileIsReadFrameByFrame() throws {
        var reader = try Self.reader(FrameFile.magic + Self.frame(1, 0, [1, 2, 3]) + Self.frame(2, 1.25, [9]))
        #expect(try reader.next() == FrameFile.Frame(kind: .h264, time: 0, data: Data([1, 2, 3])))
        #expect(try reader.next() == FrameFile.Frame(kind: .jpeg, time: 1.25, data: Data([9])))
        #expect(try reader.next() == nil)
    }

    @Test(arguments: [
        (Data("SMRF".utf8) + Data([2]), "not a SimMirror frame file"),
        (FrameFile.magic + Data([1, 0]), "ends inside a frame"),
        (FrameFile.magic + frame(3, 0, [1]), "a frame of an unknown kind 3"),
        (FrameFile.magic + frame(1, -1, [1]), "has a frame of 1 bytes at -1.0s"),
        (FrameFile.magic + frame(1, 0, [1, 2]).dropLast(), "ends inside a frame"),
    ] as [(Data, String)])
    func aFrameFileThatIsNotWholeSaysWhy(data: Data, says: String) {
        do {
            var reader = try Self.reader(data)
            while try reader.next() != nil {}
            Issue.record("it was read")
        } catch {
            #expect((error as? HelperFailure)?.message.contains(says) == true)
        }
    }

    @Test func ticksPlayARecordingSpedUpAtASteadyRate() {
        let ticks = Ticks.make(duration: 1, speed: 2, fps: 4)
        #expect(ticks.times.map(\.output) == [0, 0.25])
        #expect(ticks.times.map(\.source) == [0, 0.5])
        #expect(ticks.length == 0.5 && ticks.fps == 4)
        #expect(Ticks.make(duration: 0.1, speed: 1, fps: 30).times.count == 3)
        #expect(Ticks.make(duration: 0, speed: 1, fps: 0) == Ticks(times: [(0, 0)], fps: 1))
        #expect(ticks != Ticks.make(duration: 1, speed: 1, fps: 4))
    }

    @Test func aRenderedFileIsTheRecordingsSizeNoWiderThanAskedAndEven() {
        #expect(renderedSize(sourceWidth: 1206, sourceHeight: 2622, width: nil) == (1206, 2622))
        #expect(renderedSize(sourceWidth: 1206, sourceHeight: 2622, width: 600) == (600, 1304))
        #expect(renderedSize(sourceWidth: 301, sourceHeight: 601, width: 2000) == (302, 602))
        #expect(renderedSize(sourceWidth: 0, sourceHeight: 10, width: 10) == (2, 2))
    }

    @Test func aTapShowsARingThatFadesAfterItLifts() {
        let tap = RecordedTouch(t: 1, kind: "tap", points: [[0.5, 0.25]], duration: 0.05, by: "agent")
        #expect(TouchMarks.at(0.99, [tap]).isEmpty)
        #expect(TouchMarks.at(1.1, [tap]) == [TouchMark(shape: .ring(x: 0.5, y: 0.25, radius: TouchMarks.radius), alpha: 1, byAgent: true)])
        let fading = TouchMarks.at(1 + TouchMarks.shortest + TouchMarks.fade / 2, [tap])
        #expect(fading.count == 1 && abs(fading[0].alpha - 0.5) < 1e-9)
        #expect(TouchMarks.at(1 + TouchMarks.shortest + TouchMarks.fade + 0.01, [tap]).isEmpty)
        #expect(TouchMarks.at(1, [RecordedTouch(t: 1, kind: "tap", points: [[1]], duration: 0, by: "agent")]).isEmpty)
    }

    @Test func aLongPressGrowsItsRing() {
        let press = RecordedTouch(t: 0, kind: "long_press", points: [[0.5, 0.5]], duration: 1, by: "person")
        guard case .ring(_, _, let radius) = TouchMarks.at(1, [press])[0].shape else { Issue.record("no ring"); return }
        #expect(abs(radius - TouchMarks.radius * 1.5) < 1e-9 && TouchMarks.at(1, [press])[0].byAgent == false)
    }

    @Test func aSwipeDrawsTheWayItHasComeAndWhereItsFingerIs() {
        let swipe = RecordedTouch(t: 0, kind: "swipe", points: [[0.5, 0.8], [0.5, 0.6], [0.5, 0.2]], duration: 1, by: "person")
        let halfway = TouchMarks.at(0.5, [swipe])
        #expect(halfway.count == 2)
        guard case .trail(let path) = halfway[0].shape, case .ring(let x, let y, _) = halfway[1].shape else {
            Issue.record("no trail and ring"); return
        }
        #expect(path.count == 3 && path[1] == [0.5, 0.6] && abs(y - 0.5) < 1e-9 && x == 0.5)
        #expect(abs(halfway[0].alpha - 0.8) < 1e-9 && halfway[1].alpha == 1)
        guard case .ring(_, let end, _) = TouchMarks.at(1.2, [swipe])[1].shape else { Issue.record("no ring"); return }
        #expect(abs(end - 0.2) < 1e-9)
        let still = RecordedTouch(t: 0, kind: "swipe", points: [[0.1, 0.1], [0.1, 0.1]], duration: 1, by: "agent")
        guard case .trail(let stillPath) = TouchMarks.at(0.5, [still])[0].shape else { Issue.record("no trail"); return }
        #expect(stillPath == [[0.1, 0.1]])
        #expect(TouchMarks.along([[0, 0], [0, 0], [1, 0]], 1).1 == [1, 0])
    }

    @Test func annexBIsSplitIntoItsNALUnitsWhateverStartCodeTheyHave() {
        let unit = Data([0, 0, 0, 1, 0x67, 0x42, 0, 0, 1, 0x68, 0xCE, 0, 0, 0, 1, 0x65, 0x88, 0, 0, 0, 1, 0x09, 0xF0])
        let nals = AnnexB.nalUnits(unit)
        #expect(nals == [Data([0x67, 0x42]), Data([0x68, 0xCE]), Data([0x65, 0x88]), Data([0x09, 0xF0])])
        #expect(nals.map(AnnexB.type) == [7, 8, 5, 9])
        #expect(AnnexB.nalUnits(Data([1, 2, 3])).isEmpty && AnnexB.type(Data()) == 0)
        #expect(AnnexB.nalUnits(Data([0, 0, 1, 0, 0, 1, 0x41])) == [Data([0x41])])
    }
}
