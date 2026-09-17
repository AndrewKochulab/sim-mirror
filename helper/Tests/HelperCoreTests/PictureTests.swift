// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

let iPhone17 = ScreenGeometry(widthPx: 1206, heightPx: 2622, widthPt: 402, heightPt: 874, scale: 3)

@Suite struct ScreenTests {
    @Test func aScreenIsItsPixelsAndPointsAtItsScale() throws {
        #expect(try ScreenGeometry.of(pixelWidth: 1206, pixelHeight: 2622, scale: 3) == iPhone17)
        #expect(String(decoding: JSON.encode(iPhone17), as: UTF8.self) == #"{"height_pt":874,"height_px":2622,"scale":3,"width_pt":402,"width_px":1206}"#)
        for (width, height, scale) in [(0.0, 10.0, 2.0), (10, 0, 2), (10, 10, 0), (.nan, 10, 2), (10, .infinity, 2)] {
            #expect(throws: HelperFailure.self) { try ScreenGeometry.of(pixelWidth: width, pixelHeight: height, scale: scale) }
        }
    }

    @Test func theScreenIsTheSurfaceOfItsSizeEitherWayElseTheLargest() {
        #expect(SurfaceChoice.pick([(7680, 4320), (1206, 2622)], screen: iPhone17) == 1)
        #expect(SurfaceChoice.pick([(0, 0), (2622, 1206)], screen: iPhone17) == 1)
        #expect(SurfaceChoice.pick([(100, 100), (0, 5), (300, 200)], screen: iPhone17) == 2)
        #expect(SurfaceChoice.pick([(0, 0)], screen: iPhone17) == nil)
        #expect(SurfaceChoice.pick([], screen: iPhone17) == nil)
    }
}

@Suite struct ScreenshotPlanTests {
    @Test func aWholeScreenIsScaledToItsWidthKeepingItsShape() throws {
        let plan = try ScreenshotPlan.make(ScreenshotRequest(maxWidth: 900, quality: 75), surfaceWidth: 1206, surfaceHeight: 2622, screen: iPhone17)
        #expect(plan == ScreenshotPlan(source: PixelRect(x: 0, y: 0, width: 1206, height: 2622), width: 900, height: 1957, quality: 0.75))
    }

    @Test func aScreenNarrowerThanTheMaximumIsNotEnlarged() throws {
        let plan = try ScreenshotPlan.make(ScreenshotRequest(maxWidth: 5000, quality: 100), surfaceWidth: 1206, surfaceHeight: 2622, screen: iPhone17)
        #expect(plan.width == 1206 && plan.height == 2622 && plan.quality == 1)
    }

    @Test func aRegionIsCutInPixelsFromItsPointsAndKeptOnTheScreen() throws {
        let crop = Crop(x: 10.5, y: 20, width: 100, height: 50)
        let plan = try ScreenshotPlan.make(ScreenshotRequest(maxWidth: 160, quality: 40, crop: crop), surfaceWidth: 1206, surfaceHeight: 2622, screen: iPhone17)
        #expect(plan.source == PixelRect(x: 31, y: 60, width: 301, height: 150))
        #expect(plan.width == 160 && plan.height == 80)
        let spilling = try ScreenshotPlan.make(ScreenshotRequest(maxWidth: 900, quality: 40, crop: Crop(x: -5, y: 860, width: 20, height: 100)), surfaceWidth: 1206, surfaceHeight: 2622, screen: iPhone17)
        #expect(spilling.source == PixelRect(x: 0, y: 2580, width: 45, height: 42))
    }

    @Test func aRequestThatCannotBeMadeSaysWhy() {
        let make = { (request: ScreenshotRequest, width: Int) in
            try ScreenshotPlan.make(request, surfaceWidth: width, surfaceHeight: 2622, screen: iPhone17)
        }
        #expect(throws: HelperFailure("the screen has no picture yet", status: 503)) { try make(ScreenshotRequest(maxWidth: 1, quality: 1), 0) }
        #expect(throws: HelperFailure("max_width must be at least 1", status: 400)) { try make(ScreenshotRequest(maxWidth: 0, quality: 1), 1206) }
        #expect(throws: HelperFailure("quality must be 1 to 100", status: 400)) { try make(ScreenshotRequest(maxWidth: 9, quality: 101), 1206) }
        #expect(throws: HelperFailure.self) { try make(ScreenshotRequest(maxWidth: 9, quality: 9, crop: Crop(x: 500, y: 0, width: 10, height: 10)), 1206) }
    }
}

@Suite struct VideoTests {
    @Test func anEncoderRunsAtTheStreamsScaleInEvenPixels() throws {
        let plan = try EncoderPlan.make(StreamSettings(fps: 30, scale: 0.7463, keyFrameS: 1, bitrate: 3_000_000), surfaceWidth: 1206, surfaceHeight: 2622)
        #expect(plan == EncoderPlan(width: 900, height: 1956, fps: 30, keyFrameInterval: 1, bitrate: 3_000_000, bytesPerSecondLimit: 562_500))
        #expect(EncoderPlan.even(0.2) == 2)
        #expect(EncoderPlan.even(901) == 902)
    }

    @Test func aStreamThatCannotBeEncodedSaysWhy() {
        let make = { (settings: StreamSettings, width: Int) in try EncoderPlan.make(settings, surfaceWidth: width, surfaceHeight: 10) }
        #expect(throws: HelperFailure("the screen has no picture yet", status: 503)) { try make(StreamSettings(fps: 30, scale: 1, keyFrameS: 1, bitrate: 1_000_000), 0) }
        #expect(throws: HelperFailure("fps must be 1 to 120", status: 400)) { try make(StreamSettings(fps: 0, scale: 1, keyFrameS: 1, bitrate: 1_000_000), 10) }
        #expect(throws: HelperFailure("scale must be more than 0 and at most 1", status: 400)) { try make(StreamSettings(fps: 30, scale: 1.5, keyFrameS: 1, bitrate: 1_000_000), 10) }
        #expect(throws: HelperFailure("key_frame_s must be more than 0", status: 400)) { try make(StreamSettings(fps: 30, scale: 1, keyFrameS: 0, bitrate: 1_000_000), 10) }
        #expect(throws: HelperFailure("bitrate must be at least 100000", status: 400)) { try make(StreamSettings(fps: 30, scale: 1, keyFrameS: 1, bitrate: 5), 10) }
    }

    @Test func anAccessUnitIsItsParameterSetsItsUnitsAndADelimiter() throws {
        let avcc = Data([0, 0, 0, 2, 0x65, 0x88, 0, 0, 0, 1, 0x06])
        let unit = try AnnexB.accessUnit(avcc: avcc, parameterSets: [Data([0x67, 0x64]), Data([0x68])])
        #expect([UInt8](unit) == [0, 0, 0, 1, 0x67, 0x64, 0, 0, 0, 1, 0x68, 0, 0, 0, 1, 0x65, 0x88, 0, 0, 0, 1, 0x06, 0, 0, 0, 1, 0x09, 0xF0])
        let short = try AnnexB.accessUnit(avcc: Data([0, 1, 0x41]), lengthSize: 2)
        #expect([UInt8](short) == [0, 0, 0, 1, 0x41, 0, 0, 0, 1, 0x09, 0xF0])
    }

    @Test func anEncodedFrameThatDoesNotHoldItsUnitsIsRefused() {
        #expect(throws: HelperFailure.self) { try AnnexB.accessUnit(avcc: Data([0, 0, 0]), lengthSize: 4) }
        #expect(throws: HelperFailure.self) { try AnnexB.accessUnit(avcc: Data([0, 0, 0, 9, 1]), lengthSize: 4) }
        #expect(throws: HelperFailure.self) { try AnnexB.accessUnit(avcc: Data([0, 0, 0, 0]), lengthSize: 4) }
        #expect(throws: HelperFailure.self) { try AnnexB.accessUnit(avcc: Data(), lengthSize: 5) }
    }
}

@Suite struct FrameSchedulerTests {
    @Test func aStreamStartsWithAKeyFrame() {
        var scheduler = FrameScheduler(fps: 30, keyFrameInterval: 1, idleKeyFrames: true)
        #expect(scheduler.tick(at: 0) == .encode(key: true))
        #expect(scheduler.minimumInterval == 1.0 / 30)
    }

    @Test func aChangeIsEncodedAtOnceButNeverFasterThanTheFrameRate() {
        var scheduler = FrameScheduler(fps: 10, keyFrameInterval: 5, idleKeyFrames: true)
        scheduler.encoded(at: 0, key: true)
        #expect(scheduler.frameChanged(at: 0.05) == .wait(until: 0.1))
        #expect(scheduler.tick(at: 0.08) == .wait(until: 0.1))
        #expect(scheduler.tick(at: 0.1) == .encode(key: false))
        scheduler.encoded(at: 0.1, key: false)
        #expect(scheduler.tick(at: 0.3) == .idle)
        #expect(scheduler.frameChanged(at: 0.3) == .encode(key: false))
    }

    @Test func aStillScreenSendsAKeyFrameEachIntervalOnlyWhenAsked() {
        var idle = FrameScheduler(fps: 30, keyFrameInterval: 1, idleKeyFrames: true)
        idle.encoded(at: 0, key: true)
        #expect(idle.tick(at: 0.5) == .idle)
        #expect(idle.tick(at: 1) == .encode(key: true))
        var quiet = FrameScheduler(fps: 30, keyFrameInterval: 1, idleKeyFrames: false)
        quiet.encoded(at: 0, key: true)
        #expect(quiet.tick(at: 9) == .idle)
        #expect(quiet.frameChanged(at: 9) == .encode(key: true))
    }

    @Test func aWantedKeyFrameComesWithTheNextFrame() {
        var scheduler = FrameScheduler(fps: 0, keyFrameInterval: 60, idleKeyFrames: false)
        #expect(scheduler.minimumInterval == 1)
        scheduler.encoded(at: 0, key: true)
        scheduler.wantKeyFrame()
        #expect(scheduler.frameChanged(at: 2) == .encode(key: true))
        scheduler.encoded(at: 2, key: true)
        #expect(scheduler.frameChanged(at: 4) == .encode(key: false))
    }
}
