// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

final class RecordingTransport: HIDTransport, @unchecked Sendable {
    let name: String
    var sent: [HIDStep] = []
    var failure: HelperFailure?

    init(name: String = "dtuhid", failure: HelperFailure? = nil) {
        self.name = name
        self.failure = failure
    }

    func send(_ step: HIDStep) throws {
        if let failure { throw failure }
        sent.append(step)
    }
}

@Suite struct InputTests {
    @Test func anEventReadsWhatSimMirrorSendsAndDefaultsWhatItLeavesOut() throws {
        let events = try JSONDecoder().decode(
            [HIDEvent].self,
            from: Data(#"[{"kind":"touch","phase":"down","x":1.5,"y":2},{"kind":"button","phase":"up","button":"home"},{"kind":"key","phase":"down","code":4}]"#.utf8)
        )
        #expect(events == [
            HIDEvent(kind: .touch, phase: .down, x: 1.5, y: 2),
            HIDEvent(kind: .button, phase: .up, button: "home"),
            HIDEvent(kind: .key, phase: .down, code: 4),
        ])
    }

    @Test func aDragIsOneContactStartedMovedAndEndedInFractionsOfTheScreen() throws {
        let transport = RecordingTransport()
        let driver = InputDriver(transport: transport, screen: iPhone17)
        try driver.play([HIDEvent(kind: .touch, phase: .down, x: 201, y: 437), HIDEvent(kind: .touch, phase: .down, x: 402, y: 874)])
        try driver.play([HIDEvent(kind: .touch, phase: .up, x: 500, y: -3)])
        try driver.play([HIDEvent(kind: .touch, phase: .down, x: .nan, y: 0)])
        #expect(transport.sent == [
            .touch(.start, x: 0.5, y: 0.5),
            .touch(.position, x: 1, y: 1),
            .touch(.end, x: 1, y: 0),
            .touch(.start, x: 0, y: 0),
        ])
        #expect(InputDriver.fraction(3, of: 0) == 0)
    }

    @Test func buttonsAreConsumerUsagesAndApplePayIsTheSideButtonTwice() throws {
        #expect(try InputDriver.button("home", down: true) == [.button(.menu, down: true)])
        #expect(try InputDriver.button("lock", down: false) == [.button(.power, down: false)])
        #expect(try InputDriver.button("side", down: true) == [.button(.power, down: true)])
        #expect(try InputDriver.button("siri", down: true) == [.button(.voiceCommand, down: true)])
        #expect(try InputDriver.button("apple_pay", down: true) == [.button(.power, down: true), .button(.power, down: false), .button(.power, down: true)])
        #expect(try InputDriver.button("apple_pay", down: false) == [.button(.power, down: false)])
        #expect(throws: HelperFailure("not a button: jump", status: 400)) { try InputDriver.button("jump", down: true) }
        #expect(HIDUsage.menu == HIDUsage(page: 0x0C, code: 0x40))
        let transport = RecordingTransport()
        try InputDriver(transport: transport, screen: iPhone17).play([HIDEvent(kind: .button, phase: .down, button: "home")])
        #expect(transport.sent == [.button(.menu, down: true)])
    }

    @Test func keysAreKeyboardUsagesAndNothingElse() throws {
        let transport = RecordingTransport()
        let driver = InputDriver(transport: transport, screen: iPhone17)
        try driver.play([HIDEvent(kind: .key, phase: .down, code: 40), HIDEvent(kind: .key, phase: .up, code: 40)])
        #expect(transport.sent == [.key(40, down: true), .key(40, down: false)])
        #expect(throws: HelperFailure("not a keyboard usage: 0", status: 400)) { try driver.play([HIDEvent(kind: .key, phase: .down)]) }
    }

    @Test func aTransportThatFailsStopsTheRest() {
        let transport = RecordingTransport(failure: HelperFailure("gone"))
        let driver = InputDriver(transport: transport, screen: iPhone17)
        #expect(throws: HelperFailure("gone")) { try driver.play([HIDEvent(kind: .key, phase: .down, code: 4)]) }
        #expect(driver.transport === transport)
    }

    @Test func autoPrefersDTUHIDFromTheCoreSimulatorThatShipsIt() {
        #expect(TransportPolicy.order(.auto, coreSimulator: "1171.7") == ["dtuhid", "indigo"])
        #expect(TransportPolicy.order(.auto, coreSimulator: "1155.10") == ["dtuhid", "indigo"])
        #expect(TransportPolicy.order(.auto, coreSimulator: "1051.55") == ["indigo"])
        #expect(TransportPolicy.order(.auto, coreSimulator: nil) == ["indigo"])
        #expect(TransportPolicy.order(.dtuhid, coreSimulator: nil) == ["dtuhid"])
        #expect(TransportPolicy.order(.indigo, coreSimulator: "1171.7") == ["indigo"])
        #expect(TransportPolicy.Preference.allCases.map(\.rawValue) == ["auto", "dtuhid", "indigo"])
        #expect(DigitizerPhase.end.rawValue == 2)
    }
}
