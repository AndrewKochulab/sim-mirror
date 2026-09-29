// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

@Suite struct CaptureTests {
    let screen = ScreenGeometry(widthPx: 1179, heightPx: 2556, widthPt: 393, heightPt: 852, scale: 3)
    let least = ["capture", "--udid", "00008120-0011223344556677", "--name", "Test iPhone", "--socket", "/s", "--width-px", "1179", "--height-px", "2556", "--scale", "3"]

    @Test func aCaptureIsReadWithItsScreenAndWhatFindsItsDevice() throws {
        #expect(
            try Command.parse(least)
                == .capture(CaptureOptions(udid: "00008120-0011223344556677", name: "Test iPhone", socket: "/s", screen: screen))
        )
        let all = least + [
            "--capture-id", "C1", "--reference", "/tmp/ref.png", "--parent-pid", "42", "--idle-key-frames", "off", "--wait", "60",
            "--linger", "0", "--log-level", "debug",
        ]
        #expect(
            try Command.parse(all)
                == .capture(
                    CaptureOptions(
                        udid: "00008120-0011223344556677", name: "Test iPhone", socket: "/s", screen: screen, captureId: "C1",
                        reference: "/tmp/ref.png", parentPid: 42, idleKeyFrames: false, waitS: 60, lingerS: 0, logLevel: .debug
                    )
                )
        )
    }

    @Test(arguments: [
        (["capture", "--name", "N"], "capture needs --udid"),
        (["capture", "--udid", "U", "--name", ""], "capture needs --name"),
        (["capture", "--udid", "U", "--name", "N"], "capture needs --socket"),
        (["capture", "--udid", "U", "--name", "N", "--socket", "/s"], "capture needs --width-px"),
        (["capture", "--udid", "U", "--name", "N", "--socket", "/s", "--width-px", "wide"], "--width-px is a number, not wide"),
        (["capture", "--udid", "U", "--name", "N", "--socket", "/s", "--width-px", "inf"], "--width-px is a number, not inf"),
        (["capture", "--udid", "U", "--name", "N", "--socket", "/s", "--width-px", "10", "--height-px", "20", "--scale", "0"], "reports no screen size"),
    ] as [([String], String)])
    func aCaptureThatCannotBeReadSaysWhy(arguments: [String], says: String) {
        #expect(failure(arguments)?.contains(says) == true)
    }

    @Test func itsOptionalFlagsAreChecked() {
        #expect(failure(least + ["--reference", "ref.png"])?.contains("--reference is the absolute path of a picture, not ref.png") == true)
        #expect(failure(least + ["--linger", "601"])?.contains("--linger is 0 to 600 seconds, not 601") == true)
        #expect(failure(least + ["--linger", "soon"])?.contains("--linger is 0 to 600 seconds, not soon") == true)
        #expect(failure(least + ["--wait", "0.5"])?.contains("--wait is 1 to 120 seconds, not 0.5") == true)
        #expect(failure(least + ["--parent-pid", "1"])?.contains("not a pid: 1") == true)
        #expect(failure(least + ["--hid", "auto"])?.contains("not a flag here: --hid") == true)
    }

    func failure(_ arguments: [String]) -> String? {
        do {
            _ = try Command.parse(arguments)
            return nil
        } catch {
            return (error as? HelperFailure).map { "\($0.status) \($0.message)" }
        }
    }

    @Test func aCaptureDeviceIsFoundByItsIdThenItsNameThenItsPicture() {
        let candidates = [CaptureCandidate(id: "A", name: "Test iPhone"), CaptureCandidate(id: "B", name: "Second iPhone")]
        #expect(CaptureChoice.pick(candidates, name: "Second iPhone", captureId: nil) == .use("B"))
        #expect(CaptureChoice.pick(candidates, name: "Second iPhone", captureId: "A") == .use("A"), "the id it had before wins")
        #expect(CaptureChoice.pick(candidates, name: "Test iPhone", captureId: "gone") == .use("A"))
        #expect(CaptureChoice.pick(candidates, name: "Third iPhone", captureId: nil) == .none)
        let twins = candidates + [CaptureCandidate(id: "C", name: "Test iPhone")]
        #expect(CaptureChoice.pick(twins, name: "Test iPhone", captureId: nil) == .compare(["A", "C"]))
        #expect(CaptureChoice.pick([], name: "Test iPhone", captureId: nil) == .none)
    }

    @Test func theNearestPictureIsTheOneLeastDifferent() {
        let reference: [UInt8] = [0, 100, 200, 255]
        #expect(Likeness.distance(reference, reference) == 0)
        #expect(Likeness.distance(reference, [10, 90, 200, 255]) == 5)
        #expect(Likeness.distance(reference, [1, 2]) == nil && Likeness.distance([], []) == nil)
        #expect(Likeness.nearest(reference, among: [[255, 0, 0, 0], nil, [0, 100, 190, 255], [1]]) == 2)
        #expect(Likeness.nearest(reference, among: [nil, [1]]) == nil)
        #expect(Likeness.width * Likeness.height > 0)
    }

    @Test func aPictureWiderThanTallIsTurnedIntoPortrait() {
        #expect(CaptureTurn.of(width: 2556, height: 1180) == .quarter)
        #expect(CaptureTurn.of(width: 1180, height: 2556) == .none)
        #expect(CaptureTurn.of(width: 100, height: 100) == .none)
    }

    @Test func theCaptureRunsWhileHeldAndForAWhileAfterTheLastPicture() {
        var demand = CaptureDemand(lingerS: 30)
        #expect(!demand.running(at: 0) && demand.idleAt() == nil, "nothing has asked yet")
        demand.hold(at: 10)
        demand.hold(at: 11)
        #expect(demand.running(at: 1000) && demand.idleAt() == nil && demand.holders == 2)
        demand.release(at: 20)
        #expect(demand.running(at: 1000) && demand.idleAt() == nil, "a stream still holds it")
        demand.release(at: 25)
        #expect(demand.idleAt() == 55 && demand.running(at: 54) && !demand.running(at: 55))
        demand.release(at: 60)
        #expect(demand.holders == 0 && demand.lastWanted == 60)
        #expect(!CaptureDemand(lingerS: 0).running(at: 1))
    }

    @Test func onlyAProcessNotYetDisclaimedStartsOneThatIs() {
        #expect(Disclaim.needed([:]) && Disclaim.needed([Disclaim.variable: "0"]))
        #expect(!Disclaim.needed([Disclaim.variable: "1"]))
    }
}
