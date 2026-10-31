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
        #expect(job.mp4Width == 0 && job.movieWidth == nil && job.mp4Quality == .high)
        #expect(job.mp4Fps == 30 && job.crop == nil, "a SimMirror that does not say asks for 30 a second of all of it")
        #expect(job.kept(width: 1206, height: 2622) == (1206, 2622))
    }

    @Test func aJobMayAskForAFasterMovieOfPartOfTheScreen() throws {
        let asked = Self.job.replacingOccurrences(
            of: #""gif_width":600"#, with: #""gif_width":600,"mp4_fps":120,"crop":{"x":0.25,"y":0.5,"width":0.5,"height":0.25}"#)
        let job = try RenderJob.read(Data(asked.utf8))
        #expect(job.mp4Fps == 120 && job.crop == RecordingCrop(x: 0.25, y: 0.5, width: 0.5, height: 0.25))
        #expect(job.kept(width: 1206, height: 2622) == (603, 656))
        #expect(job.kept(width: 1, height: 1) == (1, 1), "never nothing")
    }

    @Test func aCropIsMeasuredInPixelsFromTheTopLeftAndKeptInsideThePicture() {
        let crop = RecordingCrop(x: 0.25, y: 0.5, width: 0.5, height: 0.25)
        #expect(crop.pixels(width: 400, height: 800) == (100, 400, 200, 200))
        let edge = RecordingCrop(x: 0.9, y: 0.9, width: 0.1 + 5e-7, height: 0.1 + 5e-7)
        let rect = edge.pixels(width: 100, height: 100)
        #expect(rect.x == 90 && rect.width == 10 && rect.y + rect.height == 100, "rounding never reaches past the edge")
    }

    @Test func howFastPicturesCameIsTheBusiestSecondAndTheLongestWait() {
        #expect(CaptureRate.of([]) == CaptureRate(pictures: 0, busiestFps: 0, longestGapS: 0))
        #expect(CaptureRate.of([2]) == CaptureRate(pictures: 1, busiestFps: 1, longestGapS: 0))
        // 60 a second for half a second, then nothing for 2.5 s, then 10 more over a second.
        let burst = (0..<30).map { Double($0) / 60 }
        let slow = (0..<10).map { 3 + Double($0) / 10 }
        let rate = CaptureRate.of(burst + slow)
        #expect(rate.pictures == 40 && rate.busiestFps == 30 && rate.longestGapS == 2.517)
        #expect(CaptureRate.of((0..<120).map { Double($0) / 60 }).busiestFps == 60)
        let said = String(decoding: JSON.encode(CaptureRate(pictures: 3, busiestFps: 2, longestGapS: 0.5)), as: UTF8.self)
        #expect(said == #"{"busiest_fps":2,"longest_gap_s":0.5,"pictures":3}"#)
    }

    @Test func aJobMayAskForASmallerMovieAtALowerQuality() throws {
        let asked = Self.job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"mp4_width":450,"mp4_quality":"low""#)
        let job = try RenderJob.read(Data(asked.utf8))
        #expect(job.mp4Width == 450 && job.movieWidth == 450 && job.mp4Quality == .low)
    }

    @Test func aMoviesBitrateFollowsItsSizeAndQuality() {
        #expect(MovieQuality.high.bitrate(width: 1206, height: 2622) == 12_648_528)
        #expect(MovieQuality.medium.bitrate(width: 1206, height: 2622) == 3_162_132)
        #expect(MovieQuality.low.bitrate(width: 450, height: 978) == 132_030)
        #expect(MovieQuality.low.bitrate(width: 64, height: 140) == MovieQuality.lowestBitrate)
        #expect(MovieQuality.allCases.map(\.rawValue) == ["low", "medium", "high"])
    }

    @Test(arguments: [
        ("{", "not a render job"),
        (#"{"mp4":null,"gif":null}"#, "not a render job"),
        (job.replacingOccurrences(of: #""mp4":"/r/a.mp4""#, with: #""mp4":null"#), "names no file to write"),
        (job.replacingOccurrences(of: "h264", with: "prores"), "not a codec: prores"),
        (job.replacingOccurrences(of: #""speed":2"#, with: #""speed":20"#), "speed must be 0.25 to 8"),
        (job.replacingOccurrences(of: #""gif_fps":12"#, with: #""gif_fps":60"#), "gif_fps must be 1 to 30"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":20"#), "gif_width must be 64 to 2000"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"mp4_width":5000"#), "mp4_width must be 0 to 4000"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"mp4_quality":"best""#), "not a render job"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"mp4_fps":240"#), "mp4_fps must be 1 to 120"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"mp4_fps":0"#), "mp4_fps must be 1 to 120"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"crop":{"x":0.5,"y":0,"width":0.6,"height":1}"#), "crop must be a part"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"crop":{"x":0,"y":0.5,"width":1,"height":0.6}"#), "crop must be a part"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"crop":{"x":-0.1,"y":0,"width":0.5,"height":0.5}"#), "crop must be a part"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"crop":{"x":0,"y":-0.1,"width":0.5,"height":0.5}"#), "crop must be a part"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"crop":{"x":0,"y":0,"width":0,"height":0.5}"#), "crop must be a part"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"crop":{"x":0,"y":0,"width":0.5,"height":0}"#), "crop must be a part"),
        (job.replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"crop":{"x":0,"y":0,"width":0.5}"#), "not a render job"),
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
        let counted = report.files[0].counted(CaptureRate(pictures: 9, busiestFps: 9, longestGapS: 0.1))
        #expect(counted.source?.pictures == 9 && counted.path == "/r/a.mp4" && counted.durationS == 2.5)
        #expect(String(decoding: JSON.encode(counted), as: UTF8.self).contains(#""source":{"busiest_fps":9,"longest_gap_s":0.1,"pictures":9}"#))
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

    static func lasting(_ seconds: String, speed: String = "1") throws -> RenderJob {
        let asked = job
            .replacingOccurrences(of: #""gif_width":600"#, with: #""gif_width":600,"duration_s":\#(seconds)"#)
            .replacingOccurrences(of: #""speed":2"#, with: #""speed":\#(speed)"#)
        return try RenderJob.read(Data(asked.utf8))
    }

    @Test func aRecordingOfAStillScreenRunsAsLongAsItRanByTheClock() throws {
        // 120 s recorded, the screen's last picture sent 0.3 s in: the file still plays 120 s.
        let still = try Self.lasting("120")
        #expect(still.durationS == 120 && still.length(of: 0.3) == 120)
        let movie = still.ticks(pictures: 0.3, fps: 30)
        #expect(movie.length == 120 && movie.times.count == 3600 && movie.times.last?.source ?? 0 < 120)
        #expect(still.ticks(pictures: 0.3, fps: 12).length == 120)
        #expect(try Self.lasting("120", speed: "2").ticks(pictures: 0.3, fps: 30).length == 60)
    }

    @Test func pictureLongerThanTheClockSaysOrAnUnknownLengthIsWhatCounts() throws {
        let job = try Self.lasting("2.5")
        #expect(job.length(of: 4) == 4 && job.length(of: .nan) == 2.5 && job.length(of: .infinity) == 2.5)
        // A SimMirror that does not say how long it ran renders as long as the pictures run, as before.
        let older = try RenderJob.read(Data(Self.job.utf8))
        #expect(older.durationS == 0 && older.length(of: 3) == 3)
        #expect(older.ticks(pictures: 3, fps: 4) == Ticks.make(duration: 3, speed: 2, fps: 4))
    }

    @Test(arguments: ["-1", "86401"])
    func aDurationNoRecordingCouldHaveIsRefused(seconds: String) {
        do {
            _ = try Self.lasting(seconds)
            Issue.record("\(seconds) was read")
        } catch {
            #expect((error as? HelperFailure)?.message == "duration_s must be 0 to 86400")
        }
    }

    @Test func thePlayheadHoldsTheLastPictureToTheEnd() {
        var pictures: [(time: Double, picture: String)] = [(0.4, "a"), (1, "b"), (2, "c")]
        var asked = 0
        var playhead = Playhead<String>()
        func next() -> (time: Double, picture: String)? {
            asked += 1
            return pictures.isEmpty ? nil : pictures.removeFirst()
        }
        #expect(playhead.advance(to: 0, next: next) == "a", "the first picture shows from the start")
        #expect(playhead.advance(to: 0.9, next: next) == "a")
        #expect(playhead.advance(to: 1, next: next) == "b" && playhead.shown == "b")
        #expect(playhead.advance(to: 5, next: next) == "c")
        let read = asked
        #expect(playhead.advance(to: 120, next: next) == "c", "the last picture holds long after it was sent")
        #expect(asked == read, "nothing more is read once the pictures end")
        #expect(playhead.times == [0.4, 1, 2], "every picture read is counted")
    }

    @Test func aPlayheadWithNoPicturesShowsNoneAndPassesOnWhatReadingThrows() throws {
        var empty = Playhead<Int>()
        #expect(empty.advance(to: 1) { nil } == nil && empty.shown == nil)
        var broken = Playhead<Int>()
        #expect(throws: HelperFailure.self) { try broken.advance(to: 1) { throw HelperFailure("a recorded frame cannot be read") } }
    }
}
